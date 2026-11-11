{
  description = "plugly-reflect-memory , universal cross-harness retrieval + learning KB for AI coding agents";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        lib = pkgs.lib;
        python = pkgs.python311;
        pyPkgs = python.pkgs;

        # ── nano-vectordb ──────────────────────────────────────────────────
        # Not packaged in nixpkgs. Pure-python, numpy-only dep chain.
        nano-vectordb = pyPkgs.buildPythonPackage rec {
          pname = "nano-vectordb";
          version = "0.0.4.3";
          pyproject = true;
          src = pkgs.fetchPypi {
            pname = "nano_vectordb";
            inherit version;
            # TOFU: replace with the hash nix reports on first build failure.
            hash = lib.fakeHash;
          };
          build-system = [ pyPkgs.setuptools ];
          dependencies = [ pyPkgs.numpy ];
          doCheck = false;
          pythonImportsCheck = [ "nano_vectordb" ];
        };

        # ── nano-graphrag (with broken chain stripped) ─────────────────────
        # Upstream install_requires pulls graspologic → hyppo → numba →
        # llvmlite, which only builds on py<3.10. We ship graspologic_shim.py
        # at runtime (pure networkx) to satisfy nano-graphrag's imports, so
        # we strip those declared deps here. `pythonRemoveDeps` rewrites the
        # dist-info install_requires so pip's runtime dep check passes inside
        # the plugly-reflect-memory buildPythonApplication below.
        nano-graphrag = pyPkgs.buildPythonPackage rec {
          pname = "nano-graphrag";
          version = "0.0.8.2";
          pyproject = true;
          src = pkgs.fetchPypi {
            pname = "nano_graphrag";
            inherit version;
            hash = lib.fakeHash;  # TOFU: fill on first build
          };
          build-system = [ pyPkgs.setuptools ];
          pythonRemoveDeps = [
            "graspologic"
            "hyppo"
            "numba"
            "llvmlite"
            "future"
          ];
          # The surviving set is the subset plugly-reflect-memory actually imports , see
          # global-learnings/cli/requirements.txt (authoritative) and the
          # plugly-reflect-memory pyproject.toml `dependencies` block.
          dependencies = with pyPkgs; [
            openai
            tenacity
            hnswlib
            xxhash
            networkx
            tiktoken
            pyyaml
            aiohttp
            tqdm
            nano-vectordb
          ];
          doCheck = false;
          # Skip import check , nano-graphrag imports graspologic at top
          # level and only our runtime shim can satisfy that. The real
          # import happens via reflect_kb.cli.graspologic_shim before
          # any nano_graphrag code loads.
          pythonImportsCheck = [ ];
        };

        # ── Python environment with every plugly-reflect-memory runtime dep ───────────
        # Built once, shared by the app and the dev shell. All deps listed
        # explicitly so the dep graph is auditable.
        pythonEnv = python.withPackages (ps: with ps; [
          # CLI
          click
          rich
          pyyaml
          httpx
          # Embeddings + numerics
          sentence-transformers
          numpy
          # Graph + tokenisation
          networkx
          tiktoken
          # nano-graphrag import-time deps (normally transitive, we ship
          # them directly because we install nano-graphrag with stripped
          # deps; see comment above).
          openai
          tenacity
          hnswlib
          xxhash
          # The overridden Python packages.
          nano-vectordb
          nano-graphrag
        ]);

        # ── plugly-reflect-memory application ─────────────────────────────────────────
        # Base CLI deps + the full `[graph]` extra so `nix run .#plugly-reflect-memory`
        # gets a working `reflect search` out of the box. pipx users on
        # macOS/Linux skip the heavy chain (see pyproject.toml comment).
        plugly-reflect-memory = pyPkgs.buildPythonApplication {
          pname = "plugly-reflect-memory";
          version = "0.1.0";
          pyproject = true;
          src = ./.;
          build-system = [ pyPkgs.hatchling ];
          dependencies = with pyPkgs; [
            # base
            click
            rich
            pyyaml
            httpx
            # [graph] extra
            sentence-transformers
            numpy
            networkx
            tiktoken
            openai
            tenacity
            hnswlib
            xxhash
            nano-vectordb
            nano-graphrag
          ];
          doCheck = false;
          pythonImportsCheck = [ "reflect_kb" ];
          meta = with lib; {
            description = "Universal cross-harness retrieval + learning KB for AI coding agents";
            homepage = "https://github.com/stevengonsalvez/plugly-reflect-memory";
            license = licenses.mit;
            mainProgram = "plugly-reflect-memory";
          };
        };
      in {
        packages = {
          default = plugly-reflect-memory;
          plugly-reflect-memory = plugly-reflect-memory;
          reflect-kb = plugly-reflect-memory; # Legacy flake output alias.
        };

        apps.default = {
          type = "app";
          program = "${plugly-reflect-memory}/bin/plugly-reflect-memory";
        };

        devShells.default = pkgs.mkShell {
          packages = [
            pythonEnv
            pkgs.uv
            pkgs.git
            pkgs.gh
            pkgs.pre-commit
          ];

          shellHook = ''
            echo "plugly-reflect-memory dev shell (python=${python.version})"
            echo "nano-graphrag override active , graspologic/hyppo/numba/llvmlite stripped."
            echo "For editable install over the nix env:  pip install -e . --no-deps --prefix $PWD/.venv-editable"
            echo "Spec: plans/reflect-v4-universal-install-spec.md"
          '';
        };
      });
}
