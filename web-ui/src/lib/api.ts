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

export async function getStations(): Promise<StationEntry[]> {
  const response = await fetch(`${API_BASE_URL}/stations`);

  if (!response.ok) {
    throw Error(`API server returned status code ${response.status}`);
  }

  const stations = await response.json();
  return stations;
}
