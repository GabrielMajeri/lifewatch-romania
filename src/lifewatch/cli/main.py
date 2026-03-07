import typer

from .database import app as database_app
from .ingester import app as ingester_app

app = typer.Typer()
app.add_typer(database_app, name="db", help="Database management tool.")
app.add_typer(ingester_app, name="ingest", help="Data ingestion tool.")

if __name__ == "__main__":
    app()
