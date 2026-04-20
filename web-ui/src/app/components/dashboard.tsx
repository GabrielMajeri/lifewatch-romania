import { StationEntry } from "@/lib/api";

import StationVariablesListing from "./station-variables-listing";
import StationSelect from "./station-select";

type Props = {
  stations: StationEntry[];
  stationId?: number;
};

export default function Dashboard({ stations, stationId }: Props) {
  return (
    <>
      <section>
        <StationSelect
          key={stationId}
          stations={stations}
          selectedStationId={stationId}
        />
      </section>
      {stationId && (
        <section className="my-5">
          <StationVariablesListing stationId={stationId} />
        </section>
      )}
    </>
  );
}
