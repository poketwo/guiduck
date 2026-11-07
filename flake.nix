{
  description = "Helper bot for Pokétwo's servers";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }: flake-utils.lib.eachDefaultSystem (system:
    let
      pkgs = import nixpkgs { inherit system; };

      # Python version to use. Should match requires-python in pyproject.toml.
      python = pkgs.python310;
    in
    {
      devShells.default = pkgs.mkShell {
        packages = [ python pkgs.uv ];

        env = {
          UV_PYTHON = "${python}/bin/python";
          UV_PYTHON_DOWNLOADS = "never";
          # Prebuilt wheels (cryptography, hiredis, ...) link against these.
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib ];
        };

        shellHook = ''
          uv sync
          source .venv/bin/activate
        '';
      };
    }
  );
}
