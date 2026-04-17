import { API_BASE_URL, StationInformation } from "@/lib/api";
import { Suspense } from "react";
import StationDataDisplay from "./station-data-display";

type Props = {
  stationId: number;
};

export default async function StationVariablesListing({ stationId }: Props) {
  let stationInfo: StationInformation;

  const response = await fetch(`${API_BASE_URL}/stations/${stationId}`);
  try {
    if (!response.ok) {
      throw Error(`API server returned status code ${response.status}`);
    }
    stationInfo = await response.json();
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
        <Suspense fallback={<p>Loading...</p>}>
          <StationDataDisplay
            stationId={stationId}
            variables={[stationInfo.variables[0].name]}
          />
        </Suspense>
      </header>
    </>
  );
}
