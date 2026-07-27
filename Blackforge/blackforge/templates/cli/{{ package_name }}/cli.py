import typer
from rich import print

app = typer.Typer()


@app.command()
def hello() -> None:
    print("[green]Hello from {{ project_name }}[/green]")


if __name__ == "__main__":
    app()
