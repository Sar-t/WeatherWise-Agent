// Renders the app icons from the WeatherWise sun mark (see src/components/Brand.tsx).
// Run after changing the mark or colours: npm run icons
import { writeFile } from "node:fs/promises";
import sharp from "sharp";

const ACCENT = "#2e6e8e";
// The mark's strokes on a 24-unit grid, centred around (12, 11.75).
const MARK = `
  <path d="M6.5 15.5a5.5 5.5 0 0 1 11 0" />
  <line x1="3" y1="15.5" x2="21" y2="15.5" />
  <line x1="7" y1="19" x2="17" y2="19" />
  <line x1="12" y1="4.5" x2="12" y2="6.5" />
  <line x1="5.2" y1="7.8" x2="6.6" y2="9.2" />
  <line x1="18.8" y1="7.8" x2="17.4" y2="9.2" />`;

/** A 64-unit tile: the mark in white, scaled `scale` times, centred; rounded corners or full bleed. */
function tile({ scale, rounded }) {
  const [tx, ty] = [32 - 12 * scale, 32 - 11.75 * scale];
  const background = rounded ? `<rect width="64" height="64" rx="14" fill="${ACCENT}"/>` : `<rect width="64" height="64" fill="${ACCENT}"/>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">${background}
<g transform="translate(${tx.toFixed(2)} ${ty.toFixed(2)}) scale(${scale})" fill="none" stroke="#fff" stroke-width="2.1" stroke-linecap="round">${MARK}</g></svg>`;
}

const roundedTile = tile({ scale: 2.1, rounded: true });
const fullTile = tile({ scale: 2.1, rounded: false }); // iOS rounds the corners itself
const maskableTile = tile({ scale: 1.6, rounded: false }); // the mark stays inside the 80% safe zone

const png = (svg, size) => sharp(Buffer.from(svg)).resize(size, size).png().toBuffer();

/** A .ico holding PNG images (supported by every current browser). */
function ico(images) {
  const header = Buffer.alloc(6 + 16 * images.length);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2); // icon
  header.writeUInt16LE(images.length, 4);
  let offset = header.length;
  images.forEach(({ size, data }, i) => {
    const entry = 6 + 16 * i;
    header.writeUInt8(size >= 256 ? 0 : size, entry);
    header.writeUInt8(size >= 256 ? 0 : size, entry + 1);
    header.writeUInt16LE(1, entry + 4); // colour planes
    header.writeUInt16LE(32, entry + 6); // bits per pixel
    header.writeUInt32LE(data.length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += data.length;
  });
  return Buffer.concat([header, ...images.map((i) => i.data)]);
}

await writeFile("src/app/icon.svg", roundedTile);
await writeFile("src/app/apple-icon.png", await png(fullTile, 180));
await writeFile("src/app/favicon.ico", ico(await Promise.all([16, 32, 48].map(async (size) => ({ size, data: await png(roundedTile, size) })))));
for (const size of [16, 32, 192, 512]) await writeFile(`public/icons/icon-${size}.png`, await png(roundedTile, size));
await writeFile("public/icons/maskable-512.png", await png(maskableTile, 512));
console.log("icons written");
