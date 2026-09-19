{
  description = "zulip-publisher — publish Zulip #blog topics to a website and Telegram";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});

      # The publisher package, buildable against either host or linux pkgs.
      mkPublisher = pkgs: pkgs.python3.pkgs.buildPythonApplication {
        pname = "zulip-publisher";
        version = "0.1.0";
        pyproject = true;
        src = ./.;
        nativeBuildInputs = [ pkgs.python3.pkgs.setuptools ];
        propagatedBuildInputs = [ pkgs.python3.pkgs.boto3 pkgs.python3.pkgs.pillow ];
        doCheck = false;
      };

      # Microsandbox on Apple silicon runs arm64 Linux guests.
      guestSystem = "aarch64-linux";
      guestPkgs = nixpkgs.legacyPackages.${guestSystem};

      # A generic, offline OCI image: interpreter + git + CA certs + the package.
      # No operator setup or secrets are baked in; everything is runtime env.
      publisherImage =
        let publisher = mkPublisher guestPkgs;
        in guestPkgs.dockerTools.buildLayeredImage {
          name = "zulip-publisher";
          tag = "latest";
          contents = [
            publisher guestPkgs.git guestPkgs.cacert guestPkgs.busybox
            guestPkgs.dockerTools.fakeNss   # /etc/passwd + /etc/group (root uid 0)
          ];
          config = {
            Entrypoint = [ "${publisher}/bin/zulip-publisher" ];
            Env = [
              "PATH=/bin"
              "SSL_CERT_FILE=${guestPkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
              "GIT_SSL_CAINFO=${guestPkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            ];
            WorkingDir = "/tmp";
          };
        };
    in
    {
      packages = forAll (pkgs:
        let
          publisher = mkPublisher pkgs;

          # Debug run: the CLI under `secretspec run`, in Python dev mode.
          debug = pkgs.writeShellApplication {
            name = "zulip-publisher-debug";
            runtimeInputs = [ publisher pkgs.secretspec ];
            text = ''
              export PYTHONDEVMODE=1
              export PYTHONWARNINGS="''${PYTHONWARNINGS:-default}"
              exec secretspec run -- zulip-publisher "''${@:-once}"
            '';
          };

          # Isolated run: the CLI inside a Microsandbox microVM built from the
          # local image. ONLY the listed input env is forwarded; no host mounts,
          # no ambient SSH/git config. Push uses PUSH_TOKEN over HTTPS.
          sandbox = pkgs.writeShellApplication {
            name = "zulip-publisher-sandbox";
            runtimeInputs = [ pkgs.secretspec pkgs.nix pkgs.coreutils pkgs.gzip ];
            text = ''
              if ! command -v msb >/dev/null 2>&1; then
                echo "microsandbox (msb) not found on PATH; install it first" >&2
                exit 127
              fi
              # Resolve secrets once via SecretSpec, then re-enter with them in env.
              if [ -z "''${_PUBLISHER_SECRETSPEC:-}" ]; then
                exec secretspec run -- env _PUBLISHER_SECRETSPEC=1 "$0" "$@"
              fi

              echo "building local image (linux)..." >&2
              tar=$(nix build "${self}#image" --no-link --print-out-paths)
              gunzip -c "$tar" | msb load -q -t zulip-publisher:latest

              # The explicit input surface: secrets + non-secret PUBLISHER_* config.
              secrets=(ZULIP_URL ZULIP_API_KEY ZULIP_API_USERNAME \
                       PREPARE_URL PREPARE_TOKEN \
                       R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY PUSH_TOKEN)
              envargs=()
              for k in "''${secrets[@]}"; do
                if [ -n "''${!k:-}" ]; then envargs+=( -e "$k=''${!k}" ); fi
              done
              while IFS='=' read -r k _; do
                case "$k" in PUBLISHER_*) envargs+=( -e "$k=''${!k}" );; esac
              done < <(env)
              # Ephemeral in-VM clone dirs unless the caller pinned them.
              printf '%s\n' "''${envargs[@]}" | grep -q 'PUBLISHER_SITE_CLONE_DIR=' \
                || envargs+=( -e PUBLISHER_SITE_CLONE_DIR=/tmp/site )
              printf '%s\n' "''${envargs[@]}" | grep -q 'PUBLISHER_TELEGRAM_CLONE_DIR=' \
                || envargs+=( -e PUBLISHER_TELEGRAM_CLONE_DIR=/tmp/telegram )

              exec msb run --no-tty "''${envargs[@]}" \
                zulip-publisher:latest -- "''${@:-once}"
            '';
          };
        in
        {
          default = publisher;
          image = publisherImage;
          inherit debug sandbox;
        });

      apps = forAll (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.system}.default}/bin/zulip-publisher";
        };
        debug = {
          type = "app";
          program = "${self.packages.${pkgs.system}.debug}/bin/zulip-publisher-debug";
        };
        sandbox = {
          type = "app";
          program = "${self.packages.${pkgs.system}.sandbox}/bin/zulip-publisher-sandbox";
        };
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [ ps.boto3 ps.pillow ps.pytest ]))
            pkgs.git pkgs.jq pkgs.secretspec
          ];
          shellHook = ''
            echo "zulip-publisher dev shell — python -m zulip_publisher <run|once|show|publish>"
            echo "  nix run .#debug   -- once   # SecretSpec + Python dev mode"
            echo "  nix run .#sandbox -- once   # Microsandbox microVM (local image)"
          '';
        };
      });

      checks = forAll (pkgs: {
        default = pkgs.stdenvNoCC.mkDerivation {
          name = "zulip-publisher-check";
          src = ./.;
          nativeBuildInputs = [
            (pkgs.python3.withPackages (ps: [ ps.boto3 ps.pillow ps.pytest ]))
            pkgs.git
          ];
          buildPhase = "python -m pytest";
          installPhase = "mkdir -p $out";
        };
      });
    };
}
