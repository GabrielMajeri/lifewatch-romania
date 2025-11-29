use std::{
    fs::{self, File},
    io::{BufRead, BufReader},
    path::Path,
};

use anyhow::Result;
use clap::{Parser, arg, command};
use regex::Regex;
use sqlx::{
    Pool, Postgres,
    postgres::{PgPoolCopyExt, PgPoolOptions},
};

use tokio;

#[derive(Parser, Debug)]
#[command(name = "lifewatch_ingester")]
#[command(about = "Tool for ingesting environmental monitoring data into the Lifewatch database.", long_about=None)]
struct Cli {
    #[arg(short, long)]
    input_directory: String,
}

#[tokio::main]
async fn main() -> Result<()> {
    // Log trace messages to stdout
    tracing_subscriber::fmt::init();

    let cli = Cli::parse();

    tracing::info!("Data ingestion tool for Lifewatch UB");

    tracing::info!("Loading environment variables from .env file...");
    dotenv::dotenv().expect("Failed to load .env file!");
    tracing::info!("Environment variables loaded!");

    let database_url = std::env::var("DATABASE_URL")
        .expect("`DATABASE_URL` environment variable is not configured");

    // Setup connection pool
    let pool = PgPoolOptions::new()
        .max_connections(5)
        .connect(&database_url)
        .await?;

    tracing::info!("Connected to database");

    tracing::info!("Starting to process files from input directory...");

    if let Err(e) = process_directory(Path::new(&cli.input_directory), &pool).await {
        tracing::error!("Processing data directory failed: {:?}", e);

        return Err(e);
    }

    Ok(())
}

async fn process_directory(input_directory: &Path, pool: &Pool<Postgres>) -> Result<()> {
    let entries = fs::read_dir(input_directory)?;

    for entry in entries {
        let entry = entry?;
        let path = entry.path();

        if path.is_file() && path.extension().unwrap_or_default() == "ghg" {
            let filename = path.file_name().unwrap().to_str().unwrap();

            // let exists: bool = sqlx::query_scalar(
            //     "SELECT EXISTS(SELECT 1 FROM file_ingest_log WHERE filename = $1 AND status = 'success')"
            // )
            // .bind(&filename)
            // .fetch_one(pool)
            // .await?;

            // if exists {
            //     tracing::info!("Skipping {}, already processed.", filename);

            //     continue;
            // }

            tracing::info!("Processing file '{}'...", filename);

            let count = ingest_file(pool, &path).await?;

            tracing::info!("Successfully parsed {} records", count);
        }
    }

    Ok(())
}

async fn ingest_file(pool: &Pool<Postgres>, path: &Path) -> Result<usize> {
    let file = File::open(path)?;
    let reader = BufReader::new(file);

    let mut archive =
        zip::ZipArchive::new(reader).expect("Failed to open GHG file as a Zip archive");

    let eddy_covariance_data_file_name_regex = Regex::new(r"^.*_smart3-\d+\.data$").unwrap();
    let mut eddy_covariance_data_file_name: Option<&str> = None;

    // println!("GHG archive file contents:");
    for file_name in archive.file_names() {
        // println!("  {}", file_name);

        if eddy_covariance_data_file_name_regex.is_match(file_name) {
            eddy_covariance_data_file_name = Some(file_name);
        }
    }

    let eddy_covariance_data_file_name = eddy_covariance_data_file_name
        .expect("Could not find eddy covariance data file within GHG file")
        .to_owned();

    // println!("Opening/decompressing eddy covariance data file");

    let eddy_covariance_data_file = archive
        .by_name(&eddy_covariance_data_file_name)
        .expect("Failed to open eddy covariance data file");

    let mut reader = BufReader::new(eddy_covariance_data_file);
    let mut read_line = || {
        let mut buffer = String::new();
        reader.read_line(&mut buffer)?;
        Ok::<String, std::io::Error>(buffer)
    };

    let model = read_line()?;
    let serial_number = read_line()?;
    let instrument_name = read_line()?;
    let _file_type = read_line()?;
    let _software_version = read_line()?;
    let _timestamp = read_line()?;
    let timezone = read_line()?;

    let model = model.strip_prefix("Model:").unwrap().trim();
    let serial_number = serial_number.strip_prefix("SN:").unwrap().trim();
    let instrument_name = instrument_name.strip_prefix("Instrument:").unwrap().trim();

    // println!("Model: {}", model);
    // println!("Serial number: {}", serial_number);
    // println!("Instrument name: {}", instrument_name);

    let timezone = timezone.strip_prefix("Timezone:").unwrap().trim();
    assert_eq!(timezone, "Etc/GMT-2");

    let (station_id,): (i32,) = sqlx::query_as("SELECT id FROM stations WHERE name = $1")
        .bind("Brașov")
        .fetch_one(pool)
        .await?;

    let sensor_id = {
        let mut transaction = pool.begin().await?;

        let result: Result<(i32,), _> = sqlx::query_as(
            "SELECT id FROM sensors WHERE station_id = $1 AND name = $2 AND serial_number = $3",
        )
        .bind(station_id)
        .bind(instrument_name)
        .bind(serial_number)
        .fetch_one(&mut *transaction)
        .await;

        let sensor_id: i32 = match result {
            Ok(row) => row.0,
            Err(..) => {
                println!(
                    "Adding previously unknown sensor to database. Sensor model: {}; instrument name: {}; serial number: {}",
                    model, instrument_name, serial_number
                );

                let matching_sensor_types: Vec<(i32,)> = sqlx::query_as(
                    "SELECT id FROM sensor_types WHERE $1 ILIKE '%' || LOWER(name) || '%'",
                )
                .bind(model)
                .fetch_all(&mut *transaction)
                .await?;

                if matching_sensor_types.len() == 0 {
                    panic!(
                        "No sensor type matching model name '{}' could be found",
                        model
                    );
                }
                if matching_sensor_types.len() > 1 {
                    panic!(
                        "Ambiguous sensor model name '{}', could not determine sensor type",
                        model
                    );
                }

                let sensor_type_id: i32 = matching_sensor_types.first().unwrap().0;

                let result: (i32,) = sqlx::query_as(
                    "INSERT INTO sensors (name, station_id, sensor_type_id, serial_number, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    RETURNING id")
                    .bind(instrument_name).bind(station_id).bind(sensor_type_id).bind(serial_number)
                    .fetch_one(&mut *transaction).await?;

                result.0
            }
        };

        transaction.commit().await?;

        sensor_id
    };

    let mut reader = csv::ReaderBuilder::new()
        .delimiter(b'\t')
        .from_reader(reader);

    let headers = reader.headers().unwrap();
    // println!("CSV file headers: {:#?}", headers);

    let column_names = headers.iter().collect::<Vec<_>>();

    let seconds_index = column_names
        .iter()
        .position(|&n| n == "Seconds")
        .expect("Cannot find 'Seconds' column");

    let nanoseconds_index = column_names
        .iter()
        .position(|&n| n == "Nanoseconds")
        .expect("Cannot find 'Nanoseconds' column");

    let column_name_to_variable_name_mapping: [(&str, &str); _] = [
        ("CO2 (mmol/m^3)", "CO2 concentration"),
        ("CO2 (umol/mol)", "CO2 molar fraction"),
        ("CO2 Absorptance", "CO2 absorptance"),
    ];

    let mut column_index_to_variable_id_mapping =
        Vec::<(usize, i32)>::with_capacity(column_name_to_variable_name_mapping.len());

    for (column_name, variable_name) in column_name_to_variable_name_mapping {
        let column_index = column_names
            .iter()
            .position(|&name| name == column_name)
            .unwrap_or_else(|| panic!("Cannot find column with name '{}'", column_name));

        let (variable_id,): (i32,) = sqlx::query_as("SELECT id FROM variables WHERE name = $1")
            .bind(variable_name)
            .fetch_one(pool)
            .await?;

        column_index_to_variable_id_mapping.push((column_index, variable_id));
    }

    let mut counter = 0;

    // Create a buffer for CSV writing (to send to DB)
    let mut writer = csv::WriterBuilder::new()
        .has_headers(false) // We are sending raw data rows, no header for DB
        .from_writer(vec![]);

    for result in reader.records() {
        let record = result.expect("Failed to parse line from data file");

        let seconds = &record[seconds_index];
        let seconds = seconds.parse().unwrap();
        let nanoseconds = &record[nanoseconds_index];
        let nanoseconds = nanoseconds.parse().unwrap();
        let timestamp = chrono::DateTime::from_timestamp(seconds, nanoseconds).unwrap();

        for (column_index, variable_id) in column_index_to_variable_id_mapping.iter().copied() {
            let value = &record[column_index];
            let value = value.parse::<f64>().unwrap_or_else(|error| {
                panic!(
                    "Failed to parse '{}' as a floating point number: {}",
                    value, error
                )
            });

            writer.serialize((
                sensor_id,
                variable_id,
                format!("{}", timestamp.format("%+")),
                nanoseconds.cast_signed(),
                value,
            ))?;
        }

        counter += 1;
    }

    let data = writer.into_inner()?;

    // Prepare COPY command
    // We utilize the Postgres COPY protocol for maximum speed.
    let mut copy_in = pool
        .copy_in_raw("COPY measurements (sensor_id, variable_id, timestamp, nanoseconds, value) FROM STDIN WITH (FORMAT CSV)")
        .await?;

    copy_in.send(data).await?;

    copy_in.finish().await?;

    Ok(counter)
}
