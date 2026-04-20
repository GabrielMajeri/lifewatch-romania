import { StationEntry } from "@/lib/api";

import StationSelect from "./station-select";
import StationDataDisplay from "./station-data-display";
import { Suspense } from "react";

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
        <Suspense>
          <section className="my-5">
            <StationDataDisplay stationId={stationId} />
          </section>
        </Suspense>
      )}
    </>
  );
}
