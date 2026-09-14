// Uruchamia oficjalny generator MF poza przeglądarką, dla której go napisano.
//
// Generator jest modułem front-endowym: rysuje PDF w całości po stronie
// klienta, ale sięga po dokładnie jedną funkcję przeglądarki, której Node nie
// ma — `FileReader`. Reszta tego pliku to dwie atrapy, które pozwalają modułowi
// się załadować, oraz protokół wejścia i wyjścia (D-027).
//
// Wejście: argumenty wiersza poleceń. Wyjście: PDF pod wskazaną ścieżką oraz
// jedna linia JSON na stdout. Błąd: jedna linia JSON na stderr i kod wyjścia 1,
// żeby strona pythonowa nie musiała zgadywać z tekstu wyjątku.

import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

// `generateInvoice` czyta wejściowy `File` przez `FileReader` i nie przyjmuje
// bajtów wprost, więc bez tej atrapy moduł ładuje się i pada dopiero przy
// pierwszym dokumencie. Obsługiwane są oba style nasłuchu, bo biblioteka używa
// raz `onload`, raz `addEventListener`.
class NodeFileReader {
  constructor() {
    this.result = null;
    this.error = null;
    this.onload = null;
    this.onerror = null;
    this.onloadend = null;
    this.listeners = {};
  }

  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }

  removeEventListener(type, handler) {
    this.listeners[type] = (this.listeners[type] || []).filter(
      (candidate) => candidate !== handler,
    );
  }

  fire(type) {
    const event = { type, target: this };
    const direct = this[`on${type}`];
    if (typeof direct === "function") {
      direct.call(this, event);
    }
    for (const handler of this.listeners[type] || []) {
      handler.call(this, event);
    }
  }

  read(blob, transform) {
    blob
      .arrayBuffer()
      .then((buffer) => {
        this.result = transform(buffer, blob);
        this.fire("load");
        this.fire("loadend");
      })
      .catch((cause) => {
        this.error = cause;
        this.fire("error");
        this.fire("loadend");
      });
  }

  readAsArrayBuffer(blob) {
    this.read(blob, (buffer) => buffer);
  }

  readAsText(blob) {
    this.read(blob, (buffer) => new TextDecoder().decode(buffer));
  }

  readAsDataURL(blob) {
    this.read(
      blob,
      (buffer, source) =>
        `data:${source.type};base64,${Buffer.from(buffer).toString("base64")}`,
    );
  }
}

// Generator nie rysuje niczego w DOM — fonty niesie w sobie, a wynik składa
// pdfmake. Atrapa istnieje wyłącznie po to, żeby moduł przeszedł inicjalizację,
// dlatego każda metoda jest bezczynna, a nie udawanym drzewem dokumentu.
function inertElement() {
  return {
    style: {},
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {},
    removeAttribute() {},
    getAttribute: () => null,
    appendChild(child) {
      return child;
    },
    removeChild(child) {
      return child;
    },
    addEventListener() {},
    removeEventListener() {},
    querySelector: () => null,
    querySelectorAll: () => [],
  };
}

function installBrowserShims() {
  globalThis.FileReader = NodeFileReader;
  globalThis.window = globalThis;
  globalThis.self = globalThis;
  globalThis.navigator ??= { userAgent: "node", language: "pl-PL" };
  globalThis.document = {
    createElement: inertElement,
    createElementNS: inertElement,
    createTextNode: (text) => ({ textContent: text }),
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {},
    documentElement: inertElement(),
    head: inertElement(),
    body: inertElement(),
  };
}

function readArguments(argv) {
  const [bundlePath, invoicePath, outputPath, ksefNumber, verificationUrl] = argv;
  if (!bundlePath || !invoicePath || !outputPath || !ksefNumber) {
    throw new Error(
      "Wymagane argumenty: <bundel> <faktura.xml> <wynik.pdf> " +
        "<numer-ksef> [<link-weryfikacyjny>]",
    );
  }
  return { bundlePath, invoicePath, outputPath, ksefNumber, verificationUrl };
}

async function main() {
  const options = readArguments(process.argv.slice(2));
  installBrowserShims();

  const { generateInvoice } = await import(pathToFileURL(options.bundlePath).href);
  const xml = await readFile(options.invoicePath);
  const file = new File([xml], "faktura.xml", { type: "text/xml" });
  const blob = await generateInvoice(
    file,
    { nrKSeF: options.ksefNumber, qrCode: options.verificationUrl ?? "" },
    "blob",
  );
  const pdf = Buffer.from(await blob.arrayBuffer());
  await writeFile(options.outputPath, pdf);
  process.stdout.write(
    `${JSON.stringify({ output: options.outputPath, bytes: pdf.length })}\n`,
  );
}

main().catch((cause) => {
  process.stderr.write(
    `${JSON.stringify({ error: cause?.message ?? String(cause) })}\n`,
  );
  process.exitCode = 1;
});
