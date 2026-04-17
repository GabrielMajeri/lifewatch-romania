import StationSelectContainer from "./components/station-select-container";
import StationVariablesListing from "./components/station-variables-listing";

export default async function HomePage(props: PageProps<"/">) {
  const { stationId: rawStationId } = await props.searchParams;

  let stationId = undefined;
  if (typeof rawStationId === "string" && rawStationId) {
    stationId = parseInt(rawStationId);
    if (isNaN(stationId)) {
      return (
        <p className="text-red-600 dark:text-red-500 antialiased">
          Error: invalid station ID &apos;{rawStationId}&apos;
        </p>
      );
    }
  }

  return (
    <main className="max-w-prose mx-6 sm:mx-10 mt-10 sm:mt-20">
      <header>
        <h1 className="antialiased text-3xl font-bold">
          LifeWatch Data Platform
        </h1>
        <p className="dark:subpixel-antialiased mt-4 mb-8">
          Welcome to the data platform for the LifeWatch Romania project! Please
          select a sensor station to begin exploring the available data.
        </p>
      </header>
      <section>
        <StationSelectContainer stationId={stationId} />
      </section>
      {stationId && (
        <section className="my-5">
          <StationVariablesListing stationId={stationId} />
        </section>
      )}
    </main>
  );
}
