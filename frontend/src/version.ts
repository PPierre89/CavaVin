// Version de l'app, injectée à la compilation par Vite depuis package.json
// (voir `define` dans vite.config.ts) — source de vérité unique.
declare const __APP_VERSION__: string

export const APP_VERSION = __APP_VERSION__
