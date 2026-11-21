/**
 * The Appearance tab's shared shapes: the sidebar weather's line, as the
 * weather card reads it.
 */

export interface WeatherLine {
  /** the place's name, empty when no location is set */
  place: string;
  enabled: boolean;
}
