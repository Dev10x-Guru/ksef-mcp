// Runs the Ministry's official generator outside the browser it was written for.
//
// The generator is a front-end module: it draws the whole PDF client-side, but
// reaches for exactly one browser API Node does not have — `FileReader`. The
// rest of this file is two stand-ins that let the module load, plus the input
// and output protocol (D-027).
//
// Input: command-line arguments. Output: a PDF at the given path and one line
// of JSON on stdout. Failure: one line of JSON on stderr and exit code 1, so
// the Python side never has to guess from the text of an exception.

import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

// `generateInvoice` reads its input `File` through `FileReader` and takes no
// bytes directly, so without this stand-in the module loads and only fails on
// the first document. Both listener styles are supported: the library uses
// `onload` in one place and `addEventListener` in another.
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

// The generator draws nothing into the DOM — it carries its own fonts and hands
// the layout to pdfmake. This stand-in exists only so the module gets through
// initialisation, which is why every method is inert rather than a pretend
// document tree.
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
  // The message only — but the message is not ours, and it does quote the
  // document: this build answers a wrong form code with `Unknown XML Version:
  // FA (99)` and a malformed file with `Char: e`, both read out of the file it
  // refused. Nothing can be enforced here, because the text arrives already
  // written; the Python side strips the quotations before the string reaches
  // the caller (`stated_failure`, GH-85).
  process.stderr.write(
    `${JSON.stringify({ error: cause?.message ?? String(cause) })}\n`,
  );
  process.exitCode = 1;
});
