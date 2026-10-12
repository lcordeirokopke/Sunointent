"""Ponto de entrada do dashboard: python -m app [--porta 8000]. Escuta só em 127.0.0.1."""

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app", description=__doc__.splitlines()[0])
    parser.add_argument("--porta", type=int, default=8000)
    porta = parser.parse_args().porta
    print(f"dashboard em http://127.0.0.1:{porta}")
    uvicorn.run("app.servidor:app", host="127.0.0.1", port=porta)


if __name__ == "__main__":
    main()
