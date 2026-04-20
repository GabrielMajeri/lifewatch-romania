import StationDataChart from "./variable-chart";
import {
  getMeasurements,
  getStationInfo,
  Measurements,
  StationInformation,
} from "@/lib/api";

type Props = {
  stationId: number;
};

export default async function StationDataDisplay({ stationId }: Props) {
  let stationInfo: StationInformation;
  try {
    stationInfo = await getStationInfo(stationId);
  } catch (exc) {
    let errorMessage = "unknown error";
    if (exc instanceof Error) {
      console.error(
        "Could not load station information from back end API: %s",
        exc.message,
      );
      errorMessage = exc.message;
    } else {
      console.error(
        "Could not load station information from back end API: %s",
        exc,
      );
    }

    return (
      <p className="text-red-600 antialiased dark:text-red-500">
        Failed to load station information from back end API: {errorMessage}
      </p>
    );
  }

  let measurements: Measurements;
  try {
    measurements = await getMeasurements(
      stationId,
      stationInfo.variables.map((variable) => variable.name),
    );
  } catch (exc) {
    let errorMessage = "unknown error";
    if (exc instanceof Error) {
      console.error(
        "Could not load measurements from back end API: %s",
        exc.message,
      );
      errorMessage = exc.message;
    } else {
      console.error("Could not load measurements from back end API: %s", exc);
    }
    return (
      <p className="text-red-500 wrap-anywhere">
        Error while trying to fetch measurements: {errorMessage}
      </p>
    );
  }

  return (
    <>
      <header className="m-3 my-5">
        <h2 className="my-2 text-2xl antialiased">
          Station {stationInfo.name}
        </h2>
        <p>
          Number of measured environmental variables:{" "}
          {stationInfo.variables.length}
        </p>
      </header>
      {stationInfo.variables.map((variable) => (
        <StationDataChart
          key={variable.name}
          title={variable.label}
          variableName={variable.name}
          units={variable.units}
          timestamps={measurements.timestamps[variable.name]}
          measurements={measurements.measurements[variable.name]}
        />
      ))}
    </>
  );
}
