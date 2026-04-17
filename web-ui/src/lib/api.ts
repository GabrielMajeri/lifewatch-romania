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
