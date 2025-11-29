use base64::{Engine, prelude::BASE64_STANDARD};
use dotenv;
use postgres::{Client, NoTls};
use regex::Regex;
use serde_json::Value;
use ssh2::{HostKeyType, Session};
use std::{
    env,
    io::{BufRead, BufReader, Read},
    net::TcpStream,
    path::{Path, PathBuf},
};
use tqdm::tqdm;

fn main() {
    println!("Data ingester tool for Lifewatch UB");

    println!("Loading environment variables from .env file...");
    dotenv::dotenv().expect("Failed to load .env file!");

    let mut client = Client::connect(
        "host=localhost port=5432 user=dev password=pwd dbname=lifewatch",
        NoTls,
    )
    .expect("Failed to connect to PostgreSQL database");

    println!("Listing available stations");
    for row in client
        .query("SELECT id, name, extra FROM stations", &[])
        .unwrap()
    {
        let id: i32 = row.get(0);
        let name: &str = row.get(1);
        let extra: Value = row.get(2);

        println!("Station {} - {}: {:?}", id, name, extra);
    }

    let mut contents = String::new();

    {
        const DATA_COLLECTION_SERVER_HOSTNAME: &str = "colectare.lifewatch.unibuc.ro";
        const DATA_COLLECTION_SERVER_SSH_PORT: u16 = 22;
        const DATA_COLLECTION_SERVER_SFTP_USER: &str = "sftpuser";

        // Connect to the local SSH server
        let tcp = TcpStream::connect(format!(
            "{}:{}",
            DATA_COLLECTION_SERVER_HOSTNAME, DATA_COLLECTION_SERVER_SSH_PORT
        ))
        .unwrap();

        let mut session = Session::new().unwrap();
        session.set_tcp_stream(tcp);
        session.handshake().unwrap();

        let (host_key, host_key_type) = session.host_key().expect("Could not get remote host key");
        let expected_host_key = BASE64_STANDARD.decode(b"AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBHT69HvsBZxiPDafEMRrGp9f9Lodoz6U5q6AANYGKWa/hMB2YGFpN5CElQz2Cl6hF2ssLpksmzNZwMHBXTQbsDg=").unwrap();

        match host_key_type {
            HostKeyType::Ecdsa256 => assert_eq!(host_key, expected_host_key),
            _ => panic!("Unsupported host key type: {:?}", host_key_type),
        }

        let database_ssh_public_key_file_path = env::var("DATABASE_SSH_PUBLIC_KEY");
        let database_ssh_private_key_file_path = env::var("DATABASE_SSH_PRIVATE_KEY")
            .expect("You need to provide the path to the database machine's SSH private key");

        session
            .userauth_pubkey_file(
                DATA_COLLECTION_SERVER_SFTP_USER,
                database_ssh_public_key_file_path
                    .as_deref()
                    .ok()
                    .map(Path::new),
                Path::new(&database_ssh_private_key_file_path),
                None,
            )
            .unwrap();
        assert!(session.authenticated());

        println!("Succesfully connected to the collector server over SSH!");

        let sftp = session.sftp().expect("Failed to initialize SFTP subsystem");

        {
            println!("Contents of current directory:");
            let contents = sftp.readdir(".").unwrap();
            for (path, stat) in contents {
                println!(
                    "  {} (is directory: {})",
                    path.to_str().unwrap(),
                    stat.file_type().is_dir()
                )
            }
        }

        let sftp_path = "icos/bv";
        let sftp_full_path = format!("{}/{}", DATA_COLLECTION_SERVER_SFTP_USER, sftp_path);

        println!("Contents of '{}' directory:", sftp_full_path);
        let mut data_dir = sftp.opendir(&sftp_full_path).unwrap();

        let mut ghg_file_name: Option<PathBuf> = None;

        let mut result = data_dir.readdir();
        let mut counter = 0;
        while let Ok((path, stat)) = result {
            if &path == "." || &path == ".." {
                result = data_dir.readdir();
                continue;
            }

            println!(
                "  {} (is directory: {}; file size: {} bytes)",
                path.to_str().unwrap(),
                stat.file_type().is_dir(),
                stat.size.unwrap_or_default()
            );

            if let Some(extension) = path.extension() {
                if extension == "ghg" {
                    ghg_file_name = Some(path);
                }
            }

            counter += 1;
            result = data_dir.readdir();

            if counter == 10 {
                break;
            }
        }

        let ghg_file_name = ghg_file_name.expect("Failed to find a GHG file!");
        let ghg_file_path = format!("{}/{}", sftp_full_path, ghg_file_name.to_str().unwrap());

        println!("Going to read the GHG file '{}'", ghg_file_path);

        let ghg_file = sftp
            .open(ghg_file_path)
            .expect("Failed to open GHG file for reading");

        let mut archive =
            zip::ZipArchive::new(ghg_file).expect("Failed to open GHG file as a Zip archive");

        let eddy_covariance_data_file_name_regex = Regex::new(r"^.*_smart3-\d+\.data$").unwrap();
        let mut eddy_covariance_data_file_name: Option<&str> = None;

        println!("GHG archive file contents:");
        for file_name in archive.file_names() {
            println!("  {}", file_name);

            if eddy_covariance_data_file_name_regex.is_match(file_name) {
                eddy_covariance_data_file_name = Some(file_name);
            }
        }

        let eddy_covariance_data_file_name = eddy_covariance_data_file_name
            .expect("Could not find eddy covariance data file within GHG file")
            .to_owned();

        println!("Opening/decompressing eddy covariance data file");

        let mut eddy_covariance_data_file = archive
            .by_name(&eddy_covariance_data_file_name)
            .expect("Failed to open eddy covariance data file");

        println!("Reading data file to string buffer");

        eddy_covariance_data_file
            .read_to_string(&mut contents)
            .expect("Failed to read eddy covariance data file");

        println!("File download done, closing connection and cleaning up");
    }

    // {
    //     let mut output_file = File::create("downloaded.data").unwrap();
    //     output_file.write(contents.as_bytes()).unwrap();
    // }

    // {
    //     let mut input_file = File::open("downloaded.data").unwrap();
    //     input_file.read_to_string(&mut contents).unwrap();
    // }

    println!(
        "Data file length: {} bytes ({} kilobytes, {} megabytes)",
        contents.as_bytes().len(),
        contents.as_bytes().len() / 1024,
        contents.as_bytes().len() / 1024 / 1024
    );

    let mut reader = BufReader::new(contents.as_bytes());
    let mut read_line = || {
        let mut buffer = String::new();
        reader.read_line(&mut buffer).unwrap();
        buffer
    };

    let model = read_line();
    let serial_number = read_line();
    let instrument_name = read_line();
    let _file_type = read_line();
    let _software_version = read_line();
    let _timestamp = read_line();
    let timezone = read_line();

    let model = model.strip_prefix("Model:").unwrap().trim();
    let serial_number = serial_number.strip_prefix("SN:").unwrap().trim();
    let instrument_name = instrument_name.strip_prefix("Instrument:").unwrap().trim();

    println!("Model: {}", model);
    println!("Serial number: {}", serial_number);
    println!("Instrument name: {}", instrument_name);

    let timezone = timezone.strip_prefix("Timezone:").unwrap().trim();
    assert_eq!(timezone, "Etc/GMT-2");

    let station_id: i32 = client
        .query_one("SELECT id FROM stations WHERE name = $1", &[&"Brașov"])
        .unwrap()
        .get(0);

    let sensor_id = {
        let mut transaction = client.transaction().unwrap();

        let result = transaction.query_one(
            "SELECT id FROM sensors WHERE station_id = $1 AND name = $2 AND serial_number = $3",
            &[&station_id, &instrument_name, &serial_number],
        );

        let sensor_id: i32 = match result {
            Ok(row) => row.get(0),
            Err(..) => {
                println!(
                    "Adding previously unknown sensor to database. Sensor model: {}; instrument name: {}; serial number: {}",
                    model, instrument_name, serial_number
                );

                let matching_sensor_types = transaction
                    .query(
                        "SELECT id FROM sensor_types WHERE $1 ILIKE '%' || LOWER(name) || '%'",
                        &[&model],
                    )
                    .expect("Failed to query sensor types from database");

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

                let sensor_type_id: i32 = matching_sensor_types.first().unwrap().get(0);

                let result = transaction.query_one(
                    "INSERT INTO sensors (name, station_id, sensor_type_id, serial_number, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    RETURNING id",
                    &[&instrument_name, &station_id, &sensor_type_id, &serial_number]).expect("Failed to insert new sensor into database");

                result.get(0)
            }
        };

        transaction.commit().expect("Failed to commit transaction");

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

        let variable_id = client
            .query_one(
                "SELECT id FROM variables WHERE name = $1",
                &[&variable_name],
            )
            .unwrap_or_else(|error| {
                panic!(
                    "Could not find variable with name '{}' in database: {}",
                    variable_name, error
                )
            });

        let variable_id = variable_id.get(0);

        column_index_to_variable_id_mapping.push((column_index, variable_id));
    }

    for result in tqdm(reader.records()) {
        let record = result.expect("Failed to parse line from data file");

        let seconds = &record[seconds_index];
        let seconds = seconds.parse().unwrap();
        let nanoseconds = &record[nanoseconds_index];
        let nanoseconds = nanoseconds.parse().unwrap();
        let timestamp = chrono::DateTime::from_timestamp(seconds, nanoseconds);

        for (column_index, variable_id) in column_index_to_variable_id_mapping.iter().copied() {
            let value = &record[column_index];
            let value = value.parse::<f64>().unwrap_or_else(|error| {
                panic!(
                    "Failed to parse '{}' as a floating point number: {}",
                    value, error
                )
            });

            client.execute(
                "INSERT INTO measurements (sensor_id, variable_id, timestamp, nanoseconds, value)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (sensor_id, variable_id, timestamp) DO UPDATE SET value = excluded.value",
                 &[&sensor_id, &variable_id, &timestamp, &nanoseconds.cast_signed(), &value]
                ).unwrap();
        }
    }
}
