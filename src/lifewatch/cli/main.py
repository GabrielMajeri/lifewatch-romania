import typer

from .database import app as database_app
from .ingester import app as ingester_app

app = typer.Typer()

app.add_typer(database_app, name="db", help="Database management tool.")
app.add_typer(ingester_app, name="ingest", help="Data ingestion tool.")

# Command aliases, based on https://github.com/fastapi/typer/discussions/1600
app.add_typer(database_app, name="database", hidden=True)
app.add_typer(ingester_app, name="ingester", hidden=True)

if __name__ == "__main__":
    app()
