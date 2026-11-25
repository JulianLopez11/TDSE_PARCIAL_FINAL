export const label = (value: string) => value.replaceAll("_", " ");
export const dateLocal = (value: string) => new Date(value).toLocaleString("es-CO");
