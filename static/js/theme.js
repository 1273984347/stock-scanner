/**
 * Theme utility — reads CSS custom properties for chart theming
 */
export function getThemeColor(varName) {
    return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}
