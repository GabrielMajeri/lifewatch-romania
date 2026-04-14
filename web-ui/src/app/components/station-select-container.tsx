import StationSelect, { StationEntry } from "./station-select";

const API_BASE_URL = "http://localhost:8000";

type Props = {
  stationId?: number;
};

export default async function StationSelectContainer({ stationId }: Props) {
  let stations: StationEntry[] = [];
  try {
    const response = await fetch(`${API_BASE_URL}/stations`);
    if (!response.ok) {
      throw Error(`API server returned status code ${response.status}`);
    }
    stations = await response.json();
  } catch (exc) {
    let errorMessage = "unknown error";
    if (exc instanceof Error) {
      console.error(
        "Could not load stations from back end API: %s",
        exc.message,
      );
      errorMessage = exc.message;
    } else {
      console.error("Could not load stations from back end API: %s", exc);
    }

    return (
      <p className="text-red-600 antialiased dark:text-red-500">
        Failed to load stations from back end API: {errorMessage}
      </p>
    );
  }

  let selectedStation = undefined;
  if (typeof stationId === "number") {
    selectedStation = stations.find((station) => station.id === stationId);
    if (selectedStation === undefined) {
      console.warn("Could not find station with ID %d", stationId);
    }
  }

  return (
    <StationSelect
      key={stationId}
      stations={stations}
      selectedStation={selectedStation}
    />
  );
}
