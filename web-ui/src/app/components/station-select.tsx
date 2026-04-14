"use client";

import { useEffect, useState } from "react";
import Select from "react-select";

const options = [
  { value: "chocolate", label: "Chocolate" },
  { value: "strawberry", label: "Strawberry" },
  { value: "vanilla", label: "Vanilla" },
];

export default function StationSelect() {
  const [hasMounted, setHasMounted] = useState(false);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHasMounted(true);
  }, []);

  if (!hasMounted) {
    return null;
  }

  // TODO: fetch station list from back end

  return (
    <Select
      options={options}
      classNames={{
        container: (state) => "dark:text-white",
        control: (state) => "dark:text-white",
        input: (state) => "dark:text-white",
        option: (state) =>
          "dark:bg-black dark:text-white" +
          (state.isFocused ? " dark:!bg-gray-500" : "") +
          (state.isSelected ? " dark:!bg-blue-300" : ""),
        menuList: (state) => "dark:bg-black",
        placeholder: (state) => "dark:!text-white",
        valueContainer: (state) => "dark:bg-zinc-800 dark:text-white",
      }}
    />
  );
}
