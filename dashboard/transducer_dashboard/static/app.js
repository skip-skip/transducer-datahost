(function () {
  "use strict";

  function renderChart(container) {
    var dataElement = container.querySelector("script.chart-data");
    var plotElement = container.querySelector(".plot");
    if (!dataElement || !plotElement || typeof Plotly === "undefined") {
      return;
    }

    var payload;
    try {
      payload = JSON.parse(dataElement.textContent);
    } catch (error) {
      plotElement.textContent = "Chart data could not be parsed.";
      return;
    }

    var points = payload.points || [];
    var trace = {
      x: points.map(function (point) { return point.ts; }),
      y: points.map(function (point) { return point.value; }),
      type: "scatter",
      mode: "lines",
      name: payload.label || "value",
      line: { width: 1.5 },
      hovertemplate: "%{x}<br>%{y:.4g}<extra></extra>"
    };

    var layout = {
      margin: { l: 60, r: 20, t: 20, b: 50 },
      xaxis: { title: "Local time" },
      yaxis: { title: payload.unit || "" },
      hovermode: "x unified",
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)"
    };

    Plotly.newPlot(plotElement, [trace], layout, {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d"]
    });
  }

  function renderAll(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var charts = scope.querySelectorAll("[data-chart]");
    Array.prototype.forEach.call(charts, renderChart);
  }

  document.addEventListener("DOMContentLoaded", function () { renderAll(document); });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    renderAll(event.target);
  });
})();
