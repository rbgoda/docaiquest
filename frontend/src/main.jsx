import React from "react";
import ReactDOM from "react-dom/client";
import "./styles/globals.css";
import DocumentsApp from "./DocumentsApp.jsx";

// Standalone Documents module — single product, no runtime product switch.
ReactDOM.createRoot(document.getElementById("root")).render(<DocumentsApp />);
