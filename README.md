# LifeWatch Romania data platform

## Description

This repository hosts the source code of the data platform for the Romanian branch of the [LifeWatch](https://lifewatch.ro) project. The LifeWatch network performs ecosystem monitoring through weather (meteorological) and eddy covariance stations.

## Architectural overview

The data platform is based upon a layered architecture:

- The database model is defined in [Python](https://www.python.org/), with the help of [SQLAlchemy](https://www.sqlalchemy.org/). We use the [PostgreSQL](https://www.postgresql.org/) database system, together with the [TimescaleDB](https://timescaledb.org/) extension (for handling large time series).

- The back end API server is built using [FastAPI](https://fastapi.tiangolo.com/), [Pydantic Validation](https://pydantic.dev/docs/validation/latest/concepts/models/) and [SQLAlchemy](https://www.sqlalchemy.org/).

- The front end is built using [React](https://react.dev/), [Tailwind](https://tailwindcss.com/) and [Next.js](https://nextjs.org/).

## Development setup

We recommend using [uv](https://docs.astral.sh/uv/) to manage your Python environment.

### Setting up the database

```shell
docker compose up
```

```shell
lifewatch db create
lifewatch db seed
```

### Ingesting data

```shell
lifewatch ingester meteorological
lifewatch ingester eddy-covariance
```

### Starting the back end API server

```shell
fastapi dev
```

### Starting the front end web server

```shell
pnpm run dev
```

### Environment variables

Most of the commands are designed to determine which servers to connect to and with which credentials by reading some environment variables. Create a `.env` file at the root of this repo containing the following variables:

```shell
COMPOSE_PROJECT_NAME=lifewatch
DATABASE_URL=postgresql+psycopg://<username>:<password>@<hostname>:5432/<database>

# For meteorological/weather station data reading (from the data collection FTP server)
METEO_FTP_HOST=
METEO_FTP_PORT=
METEO_FTP_USER=
METEO_FTP_PASSWORD=
```

## Command-line `lifewatch` tool

We currently provide two command-line tools:

- The database tool, which manages the database schema and performs general data operations.
- The ingester, which is able to connect to the LifeWatch RO data collection servers, read the latest records and insert them into the database.

They are both accessible as subcommands of the `lifewatch` command-line app, which can be obtained by building and installing the [Python](https://www.python.org/) package at the root of this repo.

## References and relevant links

### Normative

- [ICOS Instructions and Guidelines](https://www.icos-etc.eu/icos/documents/instructions/) &mdash; standards for collection and storage of environmental monitoring data

### Technical

- [PostgreSQL](https://www.postgresql.org/) &mdash; High-quality, free and open source relational database management system.
  - [The official PostgreSQL tutorial](https://www.postgresql.org/docs/current/tutorial.html)
- [TigerData](https://www.tigerdata.com/) (formerly TimescaleDB) &mdash; Postgres extension adding support for large-scale time series data.
- [PostGIS](https://postgis.net/) &mdash; Postgres extension adding support for spatial / geographics data.
  - [PostGIS Getting Started](https://postgis.net/documentation/getting_started/)
  - [Introduction to PostGIS workshop](https://postgis.net/workshops/postgis-intro/)
- [openEO](https://openeo.org/) &mdash; Open API for accessing large Earth Observation (EO) datasets.
- [Copernicus Dataspace](https://dataspace.copernicus.eu/) &mdash; Provides programmatic access to data from the Copernicus mission.
  - [openEO access homepage](https://dataspace.copernicus.eu/analyse/openeo)
  - [openEO API docs](https://documentation.dataspace.copernicus.eu/APIs/openEO/openEO.html)
  - [openEO Web Editor](https://openeo.dataspace.copernicus.eu/)
