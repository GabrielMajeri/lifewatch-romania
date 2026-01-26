import csv
from os import getenv
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from dotenv import load_dotenv
from rich.console import Console
from rich.prompt import Confirm
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
import typer

from .models import Base, SensorType, Station

console = Console()
err_console = Console(stderr=True)

app = typer.Typer()

ALEMBIC_INI_PATH = Path(__file__).parents[2] / "alembic.ini"

# Load environment variables before importing our code modules,
# to ensure `DATABASE_URL` gets defined beforehand.
load_dotenv()

DATABASE_URL = getenv(
    "DATABASE_URL", "postgresql+psycopg://dev:pwd@localhost:5432/lifewatch"
)


def get_alembic_config():
    "Load the Alembic configuration object."

    if not ALEMBIC_INI_PATH.exists():
        console.print(
            f"[bold red]Error:[/bold red] `alembic.ini` not found at {ALEMBIC_INI_PATH}"
        )
        raise typer.Exit(code=1)

    alembic_cfg = Config(ALEMBIC_INI_PATH)

    # Point alembic to the absolute path of the script location
    # so it finds the 'alembic/' folder correctly.
    alembic_cfg.set_main_option("script_location", "alembic")

    # Configure database connection URL
    alembic_cfg.set_main_option("sqlalchemy.url", DATABASE_URL)

    return alembic_cfg


@app.command()
def create():
    "Ensures all database tables are created (but not necessarily up-to-date with the model)."

    console.print(
        "[bold green]Checking database and creating missing tables...[/bold green]"
    )

    try:
        engine = create_engine(DATABASE_URL)
        Base.metadata.create_all(engine)

        console.print("[bold green]Tables created![/bold green]")

    except Exception as e:
        console.print(f"[bold red]Failed to create tables:[/bold red] {e}")


@app.command()
def migrate(revision: str = "head"):
    "Migrate database schema to a certain revision."

    console.print(f"[bold blue]Running migrations (target: {revision})...[/bold blue]")

    alembic_cfg = get_alembic_config()

    try:
        command.upgrade(alembic_cfg, revision)
        console.print("[bold green]Migrations applied successfully![/bold green]")
    except Exception as e:
        console.print(f"[bold red]Migration failed:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def make_migration(message: str):
    "Autogenerate a new migration file based on model changes."

    if not message:
        err_console.print(
            "[red]Please provide a message for the migration: --message '...'[/red]"
        )
        raise typer.Exit(code=1)

    console.print(f"[bold blue]Generating migration: {message}[/bold blue]")
    alembic_cfg = get_alembic_config()

    try:
        command.revision(alembic_cfg, message=message, autogenerate=True)
        console.print("[bold green]Migration file created.[/bold green]")
    except Exception as e:
        console.print(f"[bold red]Failed to generate migration:[/bold red] {e}")


@app.command()
def downgrade(
    revision: str = typer.Argument(
        "-1",
        help="The revision to downgrade to. Default is '-1' (undo last migration).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    "Reverts the database schema to a previous migration."

    # Format the confirmation message
    if revision == "-1":
        target_description = "the previous version (undo 1 step)"
    elif revision.lower() == "base":
        target_description = "the beginning (undo ALL migrations)"
    else:
        target_description = f"revision '{revision}'"

    console.print(
        f"[bold yellow]You are about to revert the database to: {target_description}[/bold yellow]"
    )
    console.print("[dim]This may drop tables or columns and result in data loss.[/dim]")

    # 2. Confirmation prompt
    if not yes:
        if not Confirm.ask("Are you sure you want to proceed?"):
            console.print("[yellow]Operation cancelled.[/yellow]")
            raise typer.Exit()

    console.print("[bold blue]Downgrading...[/bold blue]")

    alembic_cfg = get_alembic_config()

    try:
        command.downgrade(alembic_cfg, revision)
        console.print("[bold green]Downgrade complete![/bold green]")

    except Exception as e:
        console.print(f"[bold red]Downgrade failed:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def delete_last_migration(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Downgrades the DB (if necessary) and deletes the latest migration file.
    Used if you created a migration by mistake and want to 'undo' it entirely.
    """

    alembic_cfg = get_alembic_config()

    # First, we inspect the migration scripts to find the latest file (head)
    script = ScriptDirectory.from_config(alembic_cfg)

    # Get the revision ID of the latest migration script
    head_rev = script.get_current_head()

    if not head_rev:
        console.print("[yellow]No migrations found to delete.[/yellow]")
        raise typer.Exit()

    # Get the actual script object (to find the filename)
    rev_script = script.get_revision(head_rev)
    filename = rev_script.path
    message = rev_script.doc

    console.print("[bold red]!! DANGER !![/bold red]")
    console.print("You are about to delete the latest migration file:")
    console.print(f"  Revision: [bold cyan]{head_rev}[/bold cyan]")
    console.print(f"  Message:  [italic]{message}[/italic]")
    console.print(f"  File:     {filename}")

    # Now we need to check if this migration is currently applied to the DB.
    # If it is, we must downgrade first, otherwise the DB will be stuck in a version that doesn't exist on disk.
    engine = create_engine(DATABASE_URL)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        current_db_rev = context.get_current_revision()

    needs_downgrade = current_db_rev == head_rev

    if needs_downgrade:
        console.print(
            "\n[bold yellow]NOTE: This migration is currently applied to the database.[/bold yellow]"
        )
        console.print(
            "Proceeding will [bold]DOWNGRADE[/bold] the database to the previous version before deleting the file."
        )
    else:
        console.print(
            "\n[green]This migration is NOT applied to the database. Safe to delete file.[/green]"
        )

    # Ask for confirmation
    if not yes:
        if not Confirm.ask("Are you sure you want to delete this migration?"):
            console.print("[yellow]Operation cancelled.[/yellow]")
            raise typer.Exit()

    # Execute downgrade (if needed)
    if needs_downgrade:
        console.print(
            f"[bold blue]Downgrading database from {head_rev} -> -1...[/bold blue]"
        )
        try:
            command.downgrade(alembic_cfg, "-1")
            console.print("[green]Database downgraded successfully.[/green]")
        except Exception as e:
            console.print(f"[bold red]Downgrade failed:[/bold red] {e}")
            console.print("[red]Aborting file deletion to prevent corruption.[/red]")
            raise typer.Exit(1)

    # Actually delete the migration file
    migration_file_path = Path(filename)
    try:
        migration_file_path.unlink()
        console.print(f"[bold green]Deleted file: {filename}[/bold green]")

        # Remove the `__pycache__` file, if it exists
        pycache_dir = migration_file_path.parent / "__pycache__"
        glob_results = pycache_dir.glob(
            f"{migration_file_path.with_suffix('').name}*.pyc"
        )
        for pyc_file in glob_results:
            pyc_file.unlink()

    except Exception as e:
        console.print(f"[bold red]Error deleting file:[/bold red] {e}")
        raise typer.Exit(1)


def drop_all_tables(force: bool):
    console.print(
        "[bold red]WARNING: This will DROP ALL DATA in the database.[/bold red]"
    )

    if not force:
        if not Confirm.ask("Are you sure you want to continue?"):
            console.print("[yellow]Operation cancelled.[/yellow]")
            raise typer.Exit()

    console.print("[bold yellow]Dropping all tables...[/bold yellow]")

    engine = create_engine(DATABASE_URL)

    with engine.connect() as connection:
        Base.metadata.drop_all(engine)

        # Manually drop alembic_version table to ensure fresh migration start
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        connection.commit()

    console.print("[green]Tables dropped.[/green]")


@app.command()
def drop(
    force: bool = typer.Option(
        False, "--force", "-f", help="Force drop all tables without confirmation"
    ),
):
    "Drops all tables."

    try:
        drop_all_tables(force)
    except Exception as e:
        console.print(f"[bold red]Dropping tables failed:[/bold red] {e}")


@app.command()
def reset(
    force: bool = typer.Option(
        False, "--force", "-f", help="Force reset without confirmation"
    ),
):
    "Drops all tables and re-runs migrations."

    try:
        drop_all_tables(force=force)

        # Re-run migrations
        console.print("[bold blue]Re-applying migrations...[/bold blue]")
        alembic_cfg = get_alembic_config()
        command.upgrade(alembic_cfg, "head")

        console.print("[bold green]Database reset complete![/bold green]")

    except Exception as e:
        console.print(f"[bold red]Reset failed:[/bold red] {e}")


def read_csv_seed_file(name: str) -> list[dict[str, Any]]:
    print(f"Loading seed data file '{name}'")

    seed_files_directory = Path(__file__).parents[2] / "seed_data"
    seed_file_path = seed_files_directory / name

    with open(seed_file_path, newline="") as file:
        reader = csv.DictReader(file)

        return list(reader)


@app.command()
def seed():
    """Populates the database with initial data."""

    console.print("[bold blue]Seeding database...[/bold blue]")

    engine = create_engine(DATABASE_URL)
    with Session(engine) as session:
        # Import stations
        stations_data = read_csv_seed_file("stations.csv")

        for row in stations_data:
            row["extra"] = {"sftp_path": row["sftp_path"]}
            del row["sftp_path"]

        _ = session.execute(
            insert(Station).on_conflict_do_nothing(index_elements=["name"]),
            stations_data,
        )

        # Import sensor types
        sensor_types_data = read_csv_seed_file("sensor_types.csv")
        _ = session.execute(
            insert(SensorType)
            .on_conflict_do_nothing(index_elements=["name"])
            .returning(SensorType),
            sensor_types_data,
        )

        session.commit()
        console.print("[bold green]Seeding complete![/bold green]")


if __name__ == "__main__":
    app()
