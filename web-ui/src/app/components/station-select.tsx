"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import Select, { SingleValue } from "react-select";

import { StationEntry } from "@/lib/api";

type Option = { value: number; label: string };

type Props = {
  stations: StationEntry[];
  selectedStationId?: number;
};

export default function StationSelect({ stations, selectedStationId }: Props) {
  let selectedStation = undefined;
  if (selectedStationId !== undefined) {
    selectedStation = stations.find(
      (station) => station.id === selectedStationId,
    );
    if (selectedStation === undefined) {
      console.warn("Could not find station with ID %d", selectedStationId);
    }
  }

  const defaultOption =
    selectedStation !== undefined
      ? { value: selectedStation.id, label: selectedStation.name }
      : null;

  const [value, setValue] = useState<SingleValue<Option>>(
    defaultOption ?? null,
  );

  const stationGroups = Object.groupBy(
    stations,
    ({ kind }) => kind ?? "unknown",
  );
  const options = Object.entries(stationGroups).map(([key, value]) => ({
    label:
      key === "meteorological"
        ? "Meteorological"
        : key === "eddy-covariance"
          ? "Eddy covariance"
          : "Unknown",
    options: value!.map((station) => ({
      value: station.id,
      label: station.name,
    })),
  }));

  const searchParams = useSearchParams();
  const router = useRouter();

  const handleChange = useCallback(
    (newValue: SingleValue<Option>) => {
      setValue(newValue);

      const params = new URLSearchParams(searchParams.toString());

      if (newValue === null) {
        params.delete("stationId");
      } else {
        const newStationId = newValue.value;
        params.set("stationId", newStationId.toString());
      }

      router.push(`?${params.toString()}`);
    },
    [searchParams, router],
  );

  // BUG: Workaround to handle hydration issues with React Select
  const [hasMounted, setHasMounted] = useState(false);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHasMounted(true);
  }, []);

  if (!hasMounted) {
    return <p>Stations list is loading...</p>;
  }

  return (
    <Select
      options={options}
      value={value}
      classNames={{
        container: (state) => "dark:text-white",
        control: (state) =>
          "dark:text-white dark:!bg-zinc-800 dark:!border-zinc-500",
        dropdownIndicator: (state) =>
          "cursor-pointer dark:!text-white dark:bg-zinc-500",
        input: (state) => "dark:text-white",
        option: (state) =>
          "dark:bg-black dark:text-white" +
          (state.isFocused ? " dark:!bg-gray-500" : "") +
          (state.isSelected ? " dark:!bg-blue-300" : ""),
        menuList: (state) => "dark:bg-black",
        placeholder: (state) => "dark:!text-white",
        singleValue: (state) => "dark:!text-white",
        valueContainer: (state) => "dark:bg-zinc-800 dark:text-white",
      }}
      onChange={handleChange}
    />
  );
}
