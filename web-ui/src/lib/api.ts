import _ from "lodash";

export const API_BASE_URL = "http://localhost:8000";

export interface StationEntry {
  id: number;
  name: string;
  kind: string | null;
}

export interface VariableEntry {
  name: string;
  label: string;
  units: string;
  description: string;
}

export interface StationInformation extends StationEntry {
  variables: VariableEntry[];
}

type MeasurementsResponse = {
  variables: string[];
  timestamps: Record<string, string[]>;
  measurements: Record<string, number[]>;
};

export interface Measurements {
  variableNames: string[];
  timestamps: Record<string, Date[]>;
  measurements: Record<string, number[]>;
}

export async function getStations(): Promise<StationEntry[]> {
  const response = await fetch(`${API_BASE_URL}/stations`);

  if (!response.ok) {
    throw Error(`API server returned status code ${response.status}`);
  }

  const stations = await response.json();
  return stations;
}

export async function getStationInfo(
  stationId: number,
): Promise<StationInformation> {
  const response = await fetch(`${API_BASE_URL}/stations/${stationId}`);

  if (!response.ok) {
    throw Error(`API server returned status code ${response.status}`);
  }

  const stationInfo = await response.json();
  return stationInfo;
}

export async function getMeasurements(
  stationId: number,
  variables: string[] = [],
): Promise<Measurements> {
  let queryParameters = "?";

  if (variables.length > 0) {
    queryParameters += `variables=${variables.join("&variables=")}`;
  }

  const url = `${API_BASE_URL}/stations/${stationId}/measurements${queryParameters}`;

  const response = await fetch(url);

  if (!response.ok) {
    let errorDetail: undefined | string;
    try {
      const body = await response.json();
      errorDetail = body["detail"];
    } catch {}

    if (errorDetail) {
      throw Error(
        `API server returned status code ${response.status}: ${errorDetail}`,
      );
    }

    throw Error(`API server returned status code ${response.status}`);
  }

  const body: MeasurementsResponse = await response.json();

  const variableNames = body["variables"];
  const measurements = body["measurements"];

  const timestamps = _.mapValues(body["timestamps"], (timestampsArray) =>
    timestampsArray.map((ts) => new Date(ts)),
  );

  return {
    variableNames,
    timestamps,
    measurements,
  };
}
