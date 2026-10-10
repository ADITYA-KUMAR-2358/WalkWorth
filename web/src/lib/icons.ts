// Phosphor icons (MIT), regular weight, inlined so they inherit currentColor.
import arrowLeft from "@phosphor-icons/core/assets/regular/arrow-left.svg?raw";
import arrowRight from "@phosphor-icons/core/assets/regular/arrow-right.svg?raw";
import bookOpen from "@phosphor-icons/core/assets/regular/book-open-text.svg?raw";
import footprints from "@phosphor-icons/core/assets/regular/footprints.svg?raw";
import githubLogo from "@phosphor-icons/core/assets/regular/github-logo.svg?raw";
import mapPin from "@phosphor-icons/core/assets/regular/map-pin.svg?raw";
import uploadSimple from "@phosphor-icons/core/assets/regular/upload-simple.svg?raw";
import camera from "@phosphor-icons/core/assets/regular/camera.svg?raw";
import microphone from "@phosphor-icons/core/assets/regular/microphone.svg?raw";
import moon from "@phosphor-icons/core/assets/regular/moon.svg?raw";
import sun from "@phosphor-icons/core/assets/regular/sun.svg?raw";
import trash from "@phosphor-icons/core/assets/regular/trash.svg?raw";
import { raw } from "./html";

const wrap = (svg: string) => raw(`<span class="icon" aria-hidden="true">${svg}</span>`);

export const icons = {
  arrowLeft: wrap(arrowLeft),
  arrowRight: wrap(arrowRight),
  bookOpen: wrap(bookOpen),
  camera: wrap(camera),
  footprints: wrap(footprints),
  githubLogo: wrap(githubLogo),
  mapPin: wrap(mapPin),
  uploadSimple: wrap(uploadSimple),
  microphone: wrap(microphone),
  moon: wrap(moon),
  sun: wrap(sun),
  trash: wrap(trash),
};
