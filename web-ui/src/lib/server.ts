"use server";

import { API_BASE_URL } from "./api";

export async function getMeasurements(
  stationId: number,
  variables: string[] = [],
) {
  let queryParameters = "?";

  if (variables.length > 0) {
    queryParameters += `variables=${variables.join(",")}`;
  }

  const url = `${API_BASE_URL}/stations/${stationId}/measurements${queryParameters}`;

  return fetch(url);
}
