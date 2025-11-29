# Lifewatch UB data ingester

## Description

This is a [Rust](https://rust-lang.org/)-based data ingestion tool, which reads environmental monitoring data coming from meteorological and eddy covariance stations and inserts it into the central Lifewatch UB database.

## Setup guide

You need to have the latest stable version of the Rust compiler installed. Follow the instructions [here](https://rust-lang.org/learn/get-started/) to get started with Rust.

Since we use the [`ssh2`](https://docs.rs/ssh2/latest/ssh2/) crate for realizing the SSH/SFTP connection, you should have the `pkg-config`, `libssl-dev` and `libssh2-dev` packages installed.
