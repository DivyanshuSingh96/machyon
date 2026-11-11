// The page colours Opa is drawn in, without three.js: the page reads the live ones from CSS, these are the fallbacks.
export type Palette = {body: string; amber: string; pupil: string; bg: string; dark: boolean};
export const PALETTES: Record<'light' | 'dark', Palette> = {
  light: {body: '#2A3142', amber: '#F5B83D', pupil: '#2A3142', bg: '#fffaf2', dark: false},
  dark: {body: '#FFFFFF', amber: '#F5B83D', pupil: '#2A3142', bg: '#1e2230', dark: true},
};
