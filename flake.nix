{
  description = "zulip-publisher — publish Zulip #blog topics to a website and Telegram";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in
    {
      packages = forAll (pkgs: {
        default = pkgs.python3.pkgs.buildPythonApplication {
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
      });

      apps = forAll (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.system}.default}/bin/zulip-publisher";
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
