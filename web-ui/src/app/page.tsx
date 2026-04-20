import { getStations } from "@/lib/api";

import Dashboard from "./components/dashboard";

export default async function HomePage(props: PageProps<"/">) {
  const { stationId: rawStationId } = await props.searchParams;

  let stationId = undefined;
  if (typeof rawStationId === "string" && rawStationId) {
    stationId = parseInt(rawStationId);
  }

  let stations;
  try {
    stations = await getStations();
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
      <p className="p-4 sm:p-6 md:p-10 text-red-600 antialiased dark:text-red-500">
        Failed to load stations from back end API: {errorMessage}
      </p>
    );
  }

  return (
    <main className="max-w-prose mx-6 sm:mx-10 mt-10 sm:mt-20">
      <header>
        <h1 className="antialiased text-3xl font-bold">
          LifeWatch Data Platform
        </h1>
        {stationId !== undefined && isNaN(stationId) ? (
          <p className="text-red-600 dark:text-red-500 antialiased">
            Error: invalid station ID &apos;{rawStationId}&apos;
          </p>
        ) : (
          <p className="dark:subpixel-antialiased mt-4 mb-8">
            Welcome to the data platform for the LifeWatch Romania project!
            {stationId === undefined && (
              <>
                {" "}
                Please select a sensor station to begin exploring the available
                data.
              </>
            )}
          </p>
        )}
      </header>
      <Dashboard stations={stations} stationId={stationId} />
    </main>
  );
}
