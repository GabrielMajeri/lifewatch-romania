import { getMeasurements } from "@/lib/server";

import StationDataChart from "./station-data-chart";

type Props = {
  stationId: number;
  variables?: string[];
};

export default async function StationDataDisplay({
  stationId,
  variables,
}: Props) {
  const response = await getMeasurements(stationId, variables);

  if (!response.ok) {
    return (
      <p>
        Error while trying to fetch measurements: status code {response.status};{" "}
        {await response.text()}
      </p>
    );
  }

  const data = await response.json();

  return <StationDataChart data={data} />;
}
