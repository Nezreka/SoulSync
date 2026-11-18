/**
 * The person editor sheet (piece E of #2166-#2168). Settings > People calls
 * openPersonEditor(profileId); the vanilla profile cards reach it through
 * window.openPersonEditor once mountPersonEditorHost has run (app/main.tsx).
 */

export { PersonEditorHost, mountPersonEditorHost } from './person-editor';
export { closePersonEditor, openPersonEditor } from './store';
export type { PersonEditorOptions } from './store';
