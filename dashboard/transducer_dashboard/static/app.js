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

  function resizeCharts() {
    if (typeof Plotly === "undefined") {
      return;
    }
    var plots = document.querySelectorAll("[data-chart] .plot");
    Array.prototype.forEach.call(plots, function (plot) {
      Plotly.Plots.resize(plot);
    });
  }

  function setGridStatus(element, message, isError) {
    var pane = element.parentElement;
    var status = pane ? pane.querySelector("[data-grid-status]") : null;
    if (!status) {
      return;
    }
    status.textContent = message;
    status.classList.toggle("error", Boolean(isError));
  }

  function saveGridLayout(grid) {
    var element = grid.el;
    var url = element.dataset.layoutUrl;
    if (!url) {
      return;
    }

    var payload = {
      figures: grid.save(false).map(function (node) {
        return { id: node.id, x: node.x, y: node.y, w: node.w, h: node.h };
      })
    };

    fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (response) {
      if (!response.ok) {
        throw new Error("layout save failed with " + response.status);
      }
      setGridStatus(element, "Layout saved", false);
    }).catch(function () {
      setGridStatus(element, "Could not save layout", true);
    });
  }

  var layoutTimers = new WeakMap();

  function scheduleLayoutSave(grid) {
    var element = grid.el;
    var pending = layoutTimers.get(element);
    if (pending) {
      window.clearTimeout(pending);
    }
    setGridStatus(element, "Saving layout\u2026", false);
    layoutTimers.set(element, window.setTimeout(function () {
      layoutTimers.delete(element);
      saveGridLayout(grid);
    }, 500));
  }

  function initGrid(element) {
    if (element.gridstack || typeof GridStack === "undefined") {
      return;
    }

    var cols = parseInt(element.dataset.gridCols, 10);
    var rowHeight = parseInt(element.dataset.rowHeight, 10);
    var editable = element.dataset.canEdit === "true";

    var grid = GridStack.init({
      column: cols || 12,
      cellHeight: rowHeight || 220,
      margin: 6,
      float: false,
      handle: ".panel-grip",
      disableDrag: !editable,
      disableResize: !editable
    }, element);

    grid.on("change", function () {
      if (editable) {
        scheduleLayoutSave(grid);
      }
    });

    grid.on("resizestop", function (_event, item) {
      var plot = item.querySelector(".plot");
      if (plot && typeof Plotly !== "undefined") {
        Plotly.Plots.resize(plot);
      }
    });
  }

  function initGrids(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var grids = scope.querySelectorAll(".scene-grid");
    Array.prototype.forEach.call(grids, initGrid);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initGrids(document);
    renderAll(document);
  });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    initGrids(event.target);
    renderAll(event.target);
  });

  document.body.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest(".range") : null;
    if (!button) {
      return;
    }
    var group = button.parentElement;
    var buttons = group ? group.querySelectorAll(".range") : [];
    Array.prototype.forEach.call(buttons, function (item) {
      item.classList.toggle("active", item === button);
    });
  });

  var resizeTimer = null;
  window.addEventListener("resize", function () {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(resizeCharts, 150);
  });
})();
