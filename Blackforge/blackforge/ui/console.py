from rich.console import Console

console = Console()


def info(message: str) -> None:
    console.print(f"[cyan]{message}[/cyan]")


def success(message: str) -> None:
    console.print(f"[green]{message}[/green]")


def error(message: str) -> None:
    console.print(f"[bold red]{message}[/bold red]")
