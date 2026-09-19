{
  description = "zulip-publisher — publish Zulip #blog topics to a website and Telegram";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in
    {
      packages = forAll (pkgs:
        let
          publisher = pkgs.python3.pkgs.buildPythonApplication {
            pname = "zulip-publisher";
            version = "0.1.0";
            pyproject = true;
            src = ./.;
            nativeBuildInputs = [ pkgs.python3.pkgs.setuptools ];
            propagatedBuildInputs = [ pkgs.python3.pkgs.boto3 pkgs.python3.pkgs.pillow ];
            doCheck = false;
            # Runtime deps: Pillow (image verification) + boto3 (R2 upload).
            # Zulip, preparation, git, and Telegram CI are external services.
          };

          # Debug run: the CLI under `secretspec run`, in Python dev mode.
          # Secrets come from SecretSpec (never files); defaults to a single pass.
          debug = pkgs.writeShellApplication {
            name = "zulip-publisher-debug";
            runtimeInputs = [ publisher pkgs.secretspec ];
            text = ''
              export PYTHONDEVMODE=1
              export PYTHONWARNINGS="''${PYTHONWARNINGS:-default}"
              exec secretspec run -- zulip-publisher "''${@:-once}"
            '';
          };

          # Sandboxed run: the CLI inside a Microsandbox (`msb`) microVM with the
          # repo mounted and SecretSpec secrets forwarded as sandbox env. `msb` is
          # not packaged in nixpkgs, so it is expected on PATH.
          sandbox = pkgs.writeShellApplication {
            name = "zulip-publisher-sandbox";
            runtimeInputs = [ pkgs.secretspec ];
            text = ''
              if ! command -v msb >/dev/null 2>&1; then
                echo "microsandbox (msb) not found on PATH; install it first" >&2
                exit 127
              fi
              image="''${PUBLISHER_SANDBOX_IMAGE:-python:3.11-slim}"
              secrets=(ZULIP_URL ZULIP_API_KEY ZULIP_API_USERNAME \
                       PREPARE_URL PREPARE_TOKEN \
                       R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY)
              # Resolve secrets once via SecretSpec, then re-enter with them in env.
              if [ -z "''${_PUBLISHER_SECRETSPEC:-}" ]; then
                exec secretspec run -- env _PUBLISHER_SECRETSPEC=1 "$0" "$@"
              fi
              envargs=()
              for k in "''${secrets[@]}"; do
                if [ -n "''${!k:-}" ]; then envargs+=( -e "$k=''${!k}" ); fi
              done
              exec msb run --no-tty \
                -v "$PWD:/work" -w /work -e PYTHONPATH=/work \
                "''${envargs[@]}" "$image" -- \
                sh -c 'pip install -q -e . >/dev/null 2>&1 || true; exec python -m zulip_publisher "$@"' \
                _ "''${@:-once}"
            '';
          };
        in
        {
          default = publisher;
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
            echo "  nix run .#sandbox -- once   # Microsandbox microVM"
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
