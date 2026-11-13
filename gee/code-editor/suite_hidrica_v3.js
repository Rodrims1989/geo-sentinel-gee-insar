// ============================================================================
// GEO-SENTINEL | Suite de Teledetección Hídrica — GEE Code Editor — v3
// ============================================================================
// Arquitectura multi-variable. Pilares de primera clase:
//   HIDROLOGÍA   Balance mensual de la cuenca:
//                  entradas  P dual-source (CHIRPS + ERA5-Land)
//                  salidas   PET envolvente (ERA5 techo / Oudin 2005 piso)
//                            + evaporación real modelada ERA5-Land
//                  energía   radiación solar en superficie (ERA5-Land) y
//                            radiación extraterrestre FAO-56 (para Oudin)
//   DEFORMACIÓN  Módulo InSAR: visor de QC de un stack MintPy subido como Asset
//   CALIDAD      Turbidez (NDTI) y TSS (Nechad 2010, B4) sobre píxeles de agua
//   DINÁMICA     JRC Monthly Water History (tendencia de largo plazo 1984-2021)
//   VOLUMETRÍA   Lámina de agua robusta: NDWI sobre compuestos mensuales
//                medianos, con QA flags y piso dinámico anti-ceros anómalos
//
// Reglas de calidad de dato (innegociables, ver docs/methodology.md):
//   - Máscara de nubes: s2cloudless SIEMPRE (QA60 prohibido en salares).
//   - Mes sin imágenes o sin píxeles de agua → null. JAMÁS se interpola
//     ni se inventa un valor. Los huecos en los gráficos son información.
//   - Master CSV: cada fila viaja con sus metadatos (fórmulas, escalas,
//     umbrales). El dato y su pedigrí son inseparables.
//
// Pegar completo en https://code.earthengine.google.com y presionar Run.
// ============================================================================

// ----------------------------------------------------------------------------
// 0. CONSTANTES METODOLÓGICAS (una sola fuente de verdad para UI y export)
// ----------------------------------------------------------------------------
var CLOUD_PROB_MAX = 40;     // umbral s2cloudless (%)
var NDWI_THRESHOLD = 0.0;    // NDWI > umbral = agua
var SCALE_WATER    = 20;     // m — reducciones NDWI/calidad
var SCALE_CHIRPS   = 5566;   // m — nativo ~0.05°
var SCALE_ERA5     = 11132;  // m — nativo ~0.1°
var SCALE_JRC      = 30;     // m — nativo JRC
var SCALE_INSAR    = 90;     // m — reducción espacial del stack InSAR
var INSAR_RANGE_MM = 50;     // ± mm en la paleta y la leyenda InSAR

var FORMULAS = {
  ndwi: 'NDWI=(B3-B8)/(B3+B8); agua: NDWI>' + NDWI_THRESHOLD,
  ndti: 'NDTI=(B4-B3)/(B4+B3), adimensional, solo pixeles de agua',
  tss:  'TSS=355.85*B4/(1-B4/0.1728) mg/L (Nechad 2010, RELATIVO sin calibracion local)',
  pet:  'ERA5-Land potential_evaporation_sum (m, neg=evaporacion) x -1000 = mm',
  pet_oudin: 'Oudin (2005): PE=0.408*Ra*(T+5)/100 mm/dia si T+5>0; Ra FAO-56 ' +
             '(latitud del centroide, dia 15 del mes); T media mensual ERA5-Land',
  temp: 'ERA5-Land temperature_2m (K) - 273.15 = C',
  precip: 'CHIRPS Daily, suma mensual, promedio espacial (mm)',
  precip_era5: 'ERA5-Land total_precipitation_sum (m) x 1000 = mm, promedio espacial',
  aet: 'ERA5-Land total_evaporation_sum (m, neg=evaporacion) x -1000 = mm (evaporacion real modelada)',
  rad: 'ERA5-Land surface_solar_radiation_downwards_sum (J/m2) / 1e6 = MJ/m2/mes',
  balance: 'Balance = P CHIRPS - PET; severidad juzgada contra el piso (Oudin)',
  insar: 'Asset InSAR: ImageCollection, 1 imagen por epoca con system:time_start, ' +
         'bandas vertical y east_west en mm de desplazamiento ACUMULADO; ' +
         'coherencia enmascarada aguas arriba (MintPy)'
};

var QA_LABEL = 'Cálculos realizados sobre píxeles validados con s2cloudless.';
var insarLegend = null;   // widget de leyenda InSAR activo (se limpia por corrida)

// ----------------------------------------------------------------------------
// 1. CONFIGURACIÓN INICIAL
// ----------------------------------------------------------------------------
Map.setCenter(-67.5, -25.5, 10);
Map.setOptions('SATELLITE');

var drawingTools = Map.drawingTools();
drawingTools.setShown(false);
while (drawingTools.layers().length() > 0) {
  drawingTools.layers().remove(drawingTools.layers().get(0));
}

// ----------------------------------------------------------------------------
// 2. PANEL UI (el panel raíz de GEE scrollea solo cuando el contenido excede)
// ----------------------------------------------------------------------------
var panel = ui.Panel({style: {width: '420px', padding: '10px'}});
ui.root.add(panel);

panel.add(ui.Label('Suite de Teledetección Hídrica — v3', {
  fontWeight: 'bold', fontSize: '18px', margin: '0 0 2px 0'}));
panel.add(ui.Label('Puna Argentina · Sentinel-2 + CHIRPS + ERA5-Land + JRC', {
  fontSize: '11px', color: 'gray', margin: '0 0 10px 0'}));

// Fechas ---------------------------------------------------------------------
panel.add(ui.Label('Rango de fechas', {fontWeight: 'bold'}));
var startBox = ui.Textbox({placeholder: 'AAAA-MM-DD', value: '2022-01-01',
                           style: {width: '150px'}});
var endBox = ui.Textbox({placeholder: 'AAAA-MM-DD',
  value: ee.Date(Date.now()).format('YYYY-MM-dd').getInfo(),
  style: {width: '150px'}});
panel.add(ui.Panel([ui.Label('Inicio:'), startBox, ui.Label('Fin:'), endBox],
  ui.Panel.Layout.flow('horizontal')));

// ROI ------------------------------------------------------------------------
var drawButton = ui.Button({
  label: '▭ Dibujar Área de Estudio',
  style: {stretch: 'horizontal', margin: '8px 0'},
  onClick: function () {
    while (drawingTools.layers().length() > 0) {
      drawingTools.layers().remove(drawingTools.layers().get(0));
    }
    drawingTools.setShape('rectangle');
    drawingTools.draw();
    statusLabel.setValue('Dibujá el rectángulo sobre el mapa…');
  }
});
panel.add(drawButton);
drawingTools.onDraw(function () {
  drawingTools.stop();
  statusLabel.setValue('ROI definido ✔ — elegí modo y presioná Analizar.');
});
function getRoi() {
  var layers = drawingTools.layers();
  if (layers.length() === 0) return null;
  var geoms = layers.get(0).geometries();
  if (geoms.length() === 0) return null;
  return geoms.get(0);
}

// Modos de análisis -----------------------------------------------------------
panel.add(ui.Label('Modo de análisis', {fontWeight: 'bold'}));
var MODOS = {
  MASTER:  'Dashboard Maestro (todas las variables, mensual)',
  CALIDAD: 'Calidad de Agua Detallada (por imagen)',
  JRC:     'Dinámica Histórica (JRC 1984-2021)',
  INSAR:   'Módulo InSAR (stack de desplazamientos como Asset)'
};
var modeSelect = ui.Select({
  items: [MODOS.MASTER, MODOS.CALIDAD, MODOS.JRC, MODOS.INSAR],
  value: MODOS.MASTER,
  style: {stretch: 'horizontal'}
});
panel.add(modeSelect);

// Asset InSAR: solo se usa en el modo InSAR.
var insarAssetBox = ui.Textbox({
  placeholder: 'projects/<tu-proyecto>/assets/<stack_insar>',
  style: {stretch: 'horizontal', shown: false}});
panel.add(insarAssetBox);
modeSelect.onChange(function (v) {
  insarAssetBox.style().set('shown', v === MODOS.INSAR);
});

var statusLabel = ui.Label('', {color: '#1a73e8', margin: '6px 0'});
panel.add(statusLabel);
var resultsPanel = ui.Panel();
panel.add(resultsPanel);

/** Nota metodológica al pie de cada gráfico (exigencia UX). */
function methodNote(texts) {
  var body = texts.concat([QA_LABEL]).join(' · ');
  return ui.Label(body, {fontSize: '10px', color: 'gray', margin: '2px 0 12px 0'});
}

// ----------------------------------------------------------------------------
// 3. FUNCIONES NÚCLEO
// ----------------------------------------------------------------------------

/** S2 SR + s2cloudless. QA60 prohibido: confunde costra salina con nube. */
function getS2Masked(roi, start, end) {
  var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
      .filterBounds(roi).filterDate(start, end);
  var clouds = ee.ImageCollection('COPERNICUS/S2_CLOUD_PROBABILITY')
      .filterBounds(roi).filterDate(start, end);
  var joined = ee.ImageCollection(ee.Join.saveFirst('cloud_prob').apply({
    primary: s2, secondary: clouds,
    condition: ee.Filter.equals({leftField: 'system:index',
                                 rightField: 'system:index'})
  }));
  return joined.map(function (img) {
    var prob = ee.Image(img.get('cloud_prob')).select('probability');
    return img.updateMask(prob.lt(CLOUD_PROB_MAX)).divide(10000)
              .copyProperties(img, ['system:time_start']);
  });
}

function addNdwi(img) {
  return img.addBands(img.normalizedDifference(['B3', 'B8']).rename('NDWI'));
}
function addNdti(img) {
  return img.addBands(img.normalizedDifference(['B4', 'B3']).rename('NDTI'));
}
/** TSS Nechad 2010 mono-banda B4. El NIR (B8) actúa en el enmascarado de
 *  agua (NDWI), no dentro de la fórmula: estimador publicado y trazable. */
function addTss(img) {
  var red = img.select('B4');
  return img.addBands(
    red.multiply(355.85).divide(ee.Image(1).subtract(red.divide(0.1728)))
       .rename('TSS'));
}
function toAreaKm2(mask, name) {
  return mask.multiply(ee.Image.pixelArea()).divide(1e6).rename(name);
}

// ----------------------------------------------------------------------------
// 4. GRAFO MENSUAL MULTI-VARIABLE (corazón del Dashboard y del Master CSV)
//    Por mes: volumetría + balance + calidad, con nulls estrictos.
// ----------------------------------------------------------------------------
function buildMonthlyMaster(roi, start, end) {
  var s2 = getS2Masked(roi, start, end);
  var chirps = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY')
      .filterBounds(roi).select('precipitation');
  var era5 = ee.ImageCollection('ECMWF/ERA5_LAND/MONTHLY_AGGR')
      .filterBounds(roi)
      .select(['temperature_2m', 'potential_evaporation_sum',
               'total_precipitation_sum', 'total_evaporation_sum',
               'surface_solar_radiation_downwards_sum']);

  // Latitud del centroide (radianes) para la radiación extraterrestre de Oudin.
  var phi = ee.Number(roi.centroid(1).coordinates().get(1)).multiply(Math.PI / 180);

  var startEE = ee.Date(start);
  var nMonths = ee.Date(end).difference(startEE, 'month').round();

  var raw = ee.FeatureCollection(
    ee.List.sequence(0, nMonths.subtract(1)).map(function (offset) {
      var mStart = startEE.advance(offset, 'month');
      var mEnd = mStart.advance(1, 'month');
      var mS2 = s2.filterDate(mStart, mEnd);
      var hasImages = mS2.size().gt(0);

      // ── VOLUMETRÍA + CALIDAD sobre el compuesto mediano del mes ─────────
      // If imprescindible: median() de colección vacía no tiene bandas.
      var monthStats = ee.Algorithms.If(hasImages, (function () {
        var comp = addTss(addNdti(addNdwi(mS2.median())));
        var waterMask = comp.select('NDWI').gt(NDWI_THRESHOLD);

        var area = toAreaKm2(waterMask, 'water_km2')
            .reduceRegion({reducer: ee.Reducer.sum(), geometry: roi,
                           scale: SCALE_WATER, maxPixels: 1e13, bestEffort: true})
            .get('water_km2');

        // Calidad SOLO sobre agua. Si no hay píxeles de agua en el mes,
        // reduceRegion devuelve null naturalmente: null estricto, sin inventos.
        var quality = comp.select(['NDTI', 'TSS']).updateMask(waterMask)
            .reduceRegion({reducer: ee.Reducer.mean(), geometry: roi,
                           scale: SCALE_WATER, maxPixels: 1e13, bestEffort: true});

        return ee.Dictionary({
          water_km2: area,
          ndti_mean: quality.get('NDTI'),
          tss_mean: quality.get('TSS')
        });
      })(), ee.Dictionary({water_km2: null, ndti_mean: null, tss_mean: null}));
      monthStats = ee.Dictionary(monthStats);

      // ── BALANCE: CHIRPS + ERA5 (nulls naturales por latencia) ────────────
      var precip = chirps.filterDate(mStart, mEnd).sum()
          .reduceRegion({reducer: ee.Reducer.mean(), geometry: roi,
                         scale: SCALE_CHIRPS, maxPixels: 1e13, bestEffort: true})
          .get('precipitation');
      var clima = era5.filterDate(mStart, mEnd).mean()
          .reduceRegion({reducer: ee.Reducer.mean(), geometry: roi,
                         scale: SCALE_ERA5, maxPixels: 1e13, bestEffort: true});
      // IsEqual(x,null): el 0 es dato válido, solo el null se filtra.
      function conv(key, factor, offset) {
        var v = clima.get(key);
        return ee.Algorithms.If(ee.Algorithms.IsEqual(v, null), null,
          ee.Number(v).multiply(factor).add(offset || 0));
      }
      var tempC   = conv('temperature_2m', 1, -273.15);
      var petMm   = conv('potential_evaporation_sum', -1000);
      var pEra5Mm = conv('total_precipitation_sum', 1000);
      var aetMm   = conv('total_evaporation_sum', -1000);
      var radMj   = conv('surface_solar_radiation_downwards_sum', 1e-6);

      // ── OUDIN (2005): Ra FAO-56 en el día 15 del mes, álgebra ee.Number ───
      var mid = mStart.advance(14, 'day');
      var J = mid.getRelative('day', 'year').add(1);
      var ang = J.multiply(2 * Math.PI / 365);
      var dr = ang.cos().multiply(0.033).add(1);
      var delta = ang.subtract(1.39).sin().multiply(0.409);
      var ws = phi.tan().multiply(delta.tan()).multiply(-1).max(-1).min(1).acos();
      var ra = ee.Number(24 * 60 / Math.PI * 0.0820).multiply(dr).multiply(
        ws.multiply(phi.sin()).multiply(delta.sin())
          .add(phi.cos().multiply(delta.cos()).multiply(ws.sin())));
      var days = mEnd.difference(mStart, 'day');
      var petOudin = ee.Algorithms.If(ee.Algorithms.IsEqual(tempC, null), null,
        ee.Number(tempC).add(5).max(0).multiply(ra).multiply(0.408).divide(100)
          .multiply(days));

      // Balance solo si ambos términos existen (If anidado: null estricto).
      function diffOrNull(a, b) {
        return ee.Algorithms.If(ee.Algorithms.IsEqual(a, null), null,
          ee.Algorithms.If(ee.Algorithms.IsEqual(b, null), null,
            ee.Number(a).subtract(b)));
      }
      var balEra5 = diffOrNull(precip, petMm);
      var balOudin = diffOrNull(precip, petOudin);

      return ee.Feature(null, {
        'month': mStart.format('YYYY-MM'),
        'water_km2': monthStats.get('water_km2'),
        'ndti_mean': monthStats.get('ndti_mean'),
        'tss_mean': monthStats.get('tss_mean'),
        'precip_mm': precip,
        'precip_era5_mm': pEra5Mm,
        'temp_c': tempC,
        'pet_mm': petMm,
        'pet_oudin_mm': petOudin,
        'aet_era5_mm': aetMm,
        'solar_rad_mj_m2': radMj,
        'balance_era5_mm': balEra5,
        'balance_oudin_mm': balOudin,
        'n_images': mS2.size(),
        'system:time_start': mStart.millis()
      });
    }));

  // ── QA FLAGS: piso dinámico anti-ceros anómalos (espejo del backend) ─────
  var positives = raw.filter(ee.Filter.notNull(['water_km2']))
      .filter(ee.Filter.gt('water_km2', 0)).aggregate_array('water_km2');
  var floor = ee.Number(ee.Algorithms.If(
    positives.size().gt(0),
    ee.Number(positives.reduce(ee.Reducer.median())).multiply(0.2).max(0.1),
    0.1));

  return raw.map(function (f) {
    var area = f.get('water_km2');
    var flag = ee.Algorithms.If(
      ee.Algorithms.IsEqual(area, null), 'sin_imagenes',
      ee.Algorithms.If(ee.Number(area).lte(0.01).and(floor.gt(0.1)),
                       'cero_sospechoso', 'ok'));
    // Metadatos por fila: el dato viaja con su pedigrí (exigencia Master CSV).
    return f.set({
      'qa_flag': flag,
      'meta_scale_water_m': SCALE_WATER,
      'meta_cloud_prob_max': CLOUD_PROB_MAX,
      'meta_ndwi_threshold': NDWI_THRESHOLD,
      'meta_formula_ndwi': FORMULAS.ndwi,
      'meta_formula_ndti': FORMULAS.ndti,
      'meta_formula_tss': FORMULAS.tss,
      'meta_formula_pet': FORMULAS.pet,
      'meta_formula_pet_oudin': FORMULAS.pet_oudin,
      'meta_formula_precip_era5': FORMULAS.precip_era5,
      'meta_formula_aet': FORMULAS.aet,
      'meta_formula_rad': FORMULAS.rad
    });
  });
}

// ----------------------------------------------------------------------------
// 5. BOTÓN ANALIZAR + LÓGICA DE MODOS
// ----------------------------------------------------------------------------
panel.add(ui.Button({
  label: '▶ Analizar',
  style: {stretch: 'horizontal', margin: '8px 0', color: 'green'},
  onClick: runAnalysis
}));

function runAnalysis() {
  var roi = getRoi();
  if (roi === null) {
    statusLabel.setValue('⚠ Primero dibujá el área de estudio.');
    return;
  }
  var start = startBox.getValue();
  var end = endBox.getValue();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end)) {
    statusLabel.setValue('⚠ Formato de fecha inválido. Usar AAAA-MM-DD.');
    return;
  }

  Map.layers().reset();
  if (insarLegend !== null) { Map.remove(insarLegend); insarLegend = null; }
  resultsPanel.clear();
  Map.addLayer(ee.FeatureCollection([ee.Feature(roi)]).style({
    color: 'ffff00', fillColor: '00000000', width: 2}), {}, 'ROI');

  var mode = modeSelect.getValue();
  statusLabel.setValue('Procesando: ' + mode + ' …');

  // ==========================================================================
  // MODO 1 — DASHBOARD MAESTRO (todas las variables, mensual, + Master CSV)
  // ==========================================================================
  if (mode === MODOS.MASTER) {
    var monthly = buildMonthlyMaster(roi, start, end);

    // Capa de contexto: lámina de agua del período completo.
    var s2ctx = getS2Masked(roi, start, end);
    Map.addLayer(
      s2ctx.median().normalizedDifference(['B3', 'B8'])
           .gt(NDWI_THRESHOLD).selfMask().clip(roi),
      {palette: ['#00b4d8']}, 'Lámina de agua (período)');
    Map.centerObject(roi);

    // Estado + meses marcados por QA.
    monthly.filter(ee.Filter.neq('qa_flag', 'ok')).aggregate_array('month')
        .evaluate(function (flagged, err) {
      if (err) { statusLabel.setValue('⚠ Error: ' + err); return; }
      statusLabel.setValue(flagged.length > 0
        ? 'Listo ✔ — QA marcó: ' + flagged.join(', ')
        : 'Listo ✔ — sin meses marcados por QA.');
    });

    // ── Gráfico 1: BALANCE HÍDRICO (doble eje km² / mm) ──────────────────
    // Entradas (P CHIRPS barras, P ERA5 línea fina) vs salidas (PET techo
    // ámbar / piso verde). El déficit real vive entre las dos punteadas.
    resultsPanel.add(ui.Label('1 · Balance Hídrico: entradas vs salidas',
                              {fontWeight: 'bold'}));
    resultsPanel.add(ui.Chart.feature.byFeature({
      features: monthly, xProperty: 'month',
      yProperties: ['water_km2', 'precip_mm', 'precip_era5_mm',
                    'pet_mm', 'pet_oudin_mm']
    }).setChartType('ComboChart').setOptions({
      seriesType: 'line', interpolateNulls: false,
      hAxis: {title: 'Mes', slantedText: true},
      vAxes: {0: {title: 'Área de agua (km²)'}, 1: {title: 'Lámina (mm/mes)'}},
      series: {
        0: {targetAxisIndex: 0, color: '#1d4ed8', lineWidth: 3, pointSize: 3},
        1: {targetAxisIndex: 1, color: '#22d3ee', type: 'bars'},
        2: {targetAxisIndex: 1, color: '#0e7490', lineWidth: 1},
        3: {targetAxisIndex: 1, color: '#f59e0b', lineDashStyle: [6, 4]},
        4: {targetAxisIndex: 1, color: '#16a34a', lineDashStyle: [6, 4]}
      }
    }));
    resultsPanel.add(methodNote([FORMULAS.ndwi, FORMULAS.precip,
      FORMULAS.precip_era5, FORMULAS.pet, FORMULAS.pet_oudin]));

    // KPI: déficit del período como rango (piso Oudin – techo ERA5).
    var sumOf = function (k) {
      return monthly.filter(ee.Filter.notNull([k])).aggregate_sum(k);
    };
    ee.Dictionary({p: sumOf('precip_mm'), e: sumOf('pet_mm'),
                   o: sumOf('pet_oudin_mm')}).evaluate(function (d) {
      if (!d) return;
      resultsPanel.widgets().insert(2, ui.Label(
        'Déficit del período (PET − P): ' + Math.round(d.o - d.p) + ' (Oudin) a ' +
        Math.round(d.e - d.p) + ' mm (ERA5). PET/P piso = ' +
        (d.p > 0 ? (d.o / d.p).toFixed(1) : '—') + '×',
        {fontSize: '12px', color: '#b91c1c', margin: '0 0 8px 0'}));
    });

    // ── Gráfico 1b: ENERGÍA Y SALIDAS (radiación vs evaporación real) ────
    resultsPanel.add(ui.Label('1b · Energía disponible y evaporación real',
                              {fontWeight: 'bold'}));
    resultsPanel.add(ui.Chart.feature.byFeature({
      features: monthly, xProperty: 'month',
      yProperties: ['solar_rad_mj_m2', 'aet_era5_mm', 'temp_c']
    }).setChartType('ComboChart').setOptions({
      seriesType: 'line', interpolateNulls: false,
      hAxis: {title: 'Mes', slantedText: true},
      vAxes: {0: {title: 'Radiación (MJ/m²/mes)'},
              1: {title: 'Evap. real (mm) · T (°C)'}},
      series: {
        0: {targetAxisIndex: 0, color: '#eab308', type: 'bars'},
        1: {targetAxisIndex: 1, color: '#dc2626', lineWidth: 2},
        2: {targetAxisIndex: 1, color: '#6b7280', lineDashStyle: [2, 2]}
      }
    }));
    resultsPanel.add(methodNote([FORMULAS.rad, FORMULAS.aet, FORMULAS.temp,
      'Evaporación real ERA5 es modelada: no reemplaza mediciones de tanque ni ' +
      'Eddy covariance; usarla como orden de magnitud.']));

    // ── Gráfico 2: AGUA vs TURBIDEZ (doble eje km² / NDTI) ───────────────
    resultsPanel.add(ui.Label('2 · Volumetría vs Turbidez', {fontWeight: 'bold'}));
    resultsPanel.add(ui.Chart.feature.byFeature({
      features: monthly, xProperty: 'month',
      yProperties: ['water_km2', 'ndti_mean']
    }).setChartType('ComboChart').setOptions({
      seriesType: 'line', interpolateNulls: false,
      hAxis: {title: 'Mes', slantedText: true},
      vAxes: {0: {title: 'Área de agua (km²)'}, 1: {title: 'NDTI (adim.)'}},
      series: {
        0: {targetAxisIndex: 0, color: '#1d4ed8', lineWidth: 2},
        1: {targetAxisIndex: 1, color: '#a855f7', lineWidth: 2, pointSize: 3}
      }
    }));
    resultsPanel.add(methodNote([FORMULAS.ndti,
      'Contracción de lámina con suba de NDTI sugiere concentración/resuspensión.']));

    // ── Gráfico 3: TSS (eje propio; estimador relativo) ──────────────────
    resultsPanel.add(ui.Label('3 · TSS estimado', {fontWeight: 'bold'}));
    resultsPanel.add(ui.Chart.feature.byFeature({
      features: monthly, xProperty: 'month', yProperties: ['tss_mean']
    }).setChartType('LineChart').setOptions({
      interpolateNulls: false,
      hAxis: {title: 'Mes', slantedText: true},
      vAxis: {title: 'TSS (mg/L, relativo)'},
      series: {0: {color: '#f97316', lineWidth: 2, pointSize: 3}},
      legend: {position: 'none'}
    }));
    resultsPanel.add(methodNote([FORMULAS.tss]));

    // ── Master CSV: todas las variables + QA + metadatos por fila ─────────
    Export.table.toDrive({
      collection: monthly,
      description: 'master_csv_suite_hidrica',
      folder: 'geo-sentinel',
      fileFormat: 'CSV',
      selectors: ['month', 'water_km2', 'ndti_mean', 'tss_mean',
                  'precip_mm', 'precip_era5_mm', 'temp_c', 'pet_mm',
                  'pet_oudin_mm', 'aet_era5_mm', 'solar_rad_mj_m2',
                  'balance_era5_mm', 'balance_oudin_mm',
                  'n_images', 'qa_flag',
                  'meta_scale_water_m', 'meta_cloud_prob_max',
                  'meta_ndwi_threshold', 'meta_formula_ndwi',
                  'meta_formula_ndti', 'meta_formula_tss', 'meta_formula_pet',
                  'meta_formula_pet_oudin', 'meta_formula_precip_era5',
                  'meta_formula_aet', 'meta_formula_rad']
    });
    resultsPanel.add(ui.Label(
      '📥 Master CSV preparado en la pestaña Tasks: variables + QA + metadatos.',
      {fontSize: '11px', color: '#1a73e8'}));
  }

  // ==========================================================================
  // MODO 2 — CALIDAD DE AGUA DETALLADA (resolución por imagen)
  // ==========================================================================
  else if (mode === MODOS.CALIDAD) {
    var s2q = getS2Masked(roi, start, end).map(addNdwi).map(addNdti).map(addTss);

    var waterOnly = s2q.map(function (img) {
      return img.select(['NDTI', 'TSS'])
                .updateMask(img.select('NDWI').gt(NDWI_THRESHOLD))
                .copyProperties(img, ['system:time_start']);
    });

    // Mapa: NDTI medio del período sobre agua.
    Map.addLayer(
      ee.ImageCollection(waterOnly).select('NDTI').mean().clip(roi),
      {min: -0.3, max: 0.3,
       palette: ['0d0887', '7e03a8', 'cc4778', 'f89441', 'f0f921']},
      'NDTI medio (solo agua)');
    Map.centerObject(roi);

    resultsPanel.add(ui.Label('Turbidez (NDTI) por imagen', {fontWeight: 'bold'}));
    resultsPanel.add(ui.Chart.image.series({
      imageCollection: ee.ImageCollection(waterOnly).select('NDTI'),
      region: roi, reducer: ee.Reducer.mean(), scale: SCALE_WATER
    }).setOptions({
      vAxis: {title: 'NDTI (adimensional)'}, hAxis: {title: 'Fecha'},
      interpolateNulls: false, lineWidth: 1.5, pointSize: 3,
      colors: ['#a855f7'], legend: {position: 'none'}
    }));
    resultsPanel.add(methodNote([FORMULAS.ndti]));

    resultsPanel.add(ui.Label('TSS estimado por imagen', {fontWeight: 'bold'}));
    resultsPanel.add(ui.Chart.image.series({
      imageCollection: ee.ImageCollection(waterOnly).select('TSS'),
      region: roi, reducer: ee.Reducer.mean(), scale: SCALE_WATER
    }).setOptions({
      vAxis: {title: 'TSS (mg/L, relativo)'}, hAxis: {title: 'Fecha'},
      interpolateNulls: false, lineWidth: 1.5, pointSize: 3,
      colors: ['#f97316'], legend: {position: 'none'}
    }));
    resultsPanel.add(methodNote([FORMULAS.tss,
      'Imagen sin píxeles de agua válidos → punto ausente (null estricto).']));

    statusLabel.setValue('Listo ✔ — calidad de agua por imagen.');
  }

  // ==========================================================================
  // MODO 3 — DINÁMICA HISTÓRICA (JRC 1984-2021)
  // ==========================================================================
  else if (mode === MODOS.JRC) {
    var jrc = ee.ImageCollection('JRC/GSW1_4/MonthlyHistory')
        .filterBounds(roi).filterDate(start, end);

    jrc.size().evaluate(function (n, err) {
      if (err) { statusLabel.setValue('⚠ Error JRC: ' + err); return; }
      statusLabel.setValue(n === 0
        ? '⚠ Sin imágenes JRC en el rango (cobertura 1984-2021). Para el ' +
          'período reciente usá el Dashboard Maestro.'
        : 'Listo ✔ — ' + n + ' meses JRC.');
    });

    Map.addLayer(
      jrc.map(function (img) { return img.select('water').eq(2); })
         .mean().multiply(100).clip(roi).selfMask(),
      {min: 0, max: 100, palette: ['ffffff', 'b3e5fc', '0288d1', '01579b']},
      'Frecuencia de agua JRC (%)');
    Map.centerObject(roi);

    var jrcArea = jrc.map(function (img) {
      return toAreaKm2(img.select('water').eq(2).unmask(0), 'area_km2')
          .copyProperties(img, ['system:time_start']);
    });

    resultsPanel.add(ui.Chart.image.series({
      imageCollection: jrcArea, region: roi,
      reducer: ee.Reducer.sum(), scale: SCALE_JRC
    }).setOptions({
      title: 'Presencia mensual de agua (km²) — JRC 1984-2021',
      vAxis: {title: 'Área (km²)'}, hAxis: {title: 'Mes'},
      interpolateNulls: false, lineWidth: 1, pointSize: 2,
      colors: ['#023e8a'], legend: {position: 'none'}
    }));
    resultsPanel.add(methodNote([
      'JRC/GSW1_4/MonthlyHistory, banda water==2, escala 30 m.',
      'Contrastar caídas con precipitación y bombeo antes de atribuir causalidad.']));
  }

  // ==========================================================================
  // MODO 4 — MÓDULO INSAR (visor de QC del stack; la producción vive fuera)
  // ==========================================================================
  else if (mode === MODOS.INSAR) {
    var assetId = insarAssetBox.getValue();
    if (!assetId) {
      statusLabel.setValue('⚠ Ingresá el path del Asset InSAR.');
      return;
    }
    var stack = ee.ImageCollection(assetId)
        .filterDate(start, end).sort('system:time_start');

    // Por época: media vertical y E-W + percentil 5 vertical. La media diluye
    // cuencos localizados; el p5 muestra al peor actor del ROI.
    var series = stack.map(function (img) {
      var mean = img.select(['vertical', 'east_west']).reduceRegion({
        reducer: ee.Reducer.mean(), geometry: roi, scale: SCALE_INSAR,
        maxPixels: 1e13, bestEffort: true});
      var p5 = img.select('vertical').reduceRegion({
        reducer: ee.Reducer.percentile([5]), geometry: roi, scale: SCALE_INSAR,
        maxPixels: 1e13, bestEffort: true});
      return ee.Feature(null, {
        'date': img.date().format('YYYY-MM-dd'),
        'vertical_mean_mm': mean.get('vertical'),
        'east_west_mean_mm': mean.get('east_west'),
        'vertical_p5_mm': p5.get('vertical'),
        'system:time_start': img.get('system:time_start')
      });
    });

    stack.size().evaluate(function (n, err) {
      if (err) { statusLabel.setValue('⚠ Asset InSAR: ' + err); return; }
      statusLabel.setValue(n === 0
        ? '⚠ El asset no tiene épocas en el rango de fechas.'
        : 'Listo ✔ — ' + n + ' épocas InSAR.');
    });

    Map.addLayer(ee.Image(stack.sort('system:time_start', false).first())
        .select('vertical').clip(roi),
      {min: -INSAR_RANGE_MM, max: INSAR_RANGE_MM,
       palette: ['b2182b', 'ef8a62', 'fddbc7', 'ffffff',
                 'd1e5f0', '67a9cf', '2166ac']},
      'Desplazamiento vertical acumulado (última época)');
    Map.add(buildInsarLegend(INSAR_RANGE_MM));
    Map.centerObject(roi);

    resultsPanel.add(ui.Label('Deformación en el ROI', {fontWeight: 'bold'}));
    resultsPanel.add(ui.Chart.feature.byFeature({
      features: series, xProperty: 'system:time_start',
      yProperties: ['vertical_mean_mm', 'east_west_mean_mm', 'vertical_p5_mm']
    }).setOptions({
      interpolateNulls: false, hAxis: {title: 'Fecha'},
      vAxis: {title: 'Desplazamiento acumulado (mm)'},
      series: {
        0: {color: '#b2182b', lineWidth: 2, pointSize: 3},
        1: {color: '#6b7280', lineWidth: 1.5},
        2: {color: '#7f1d1d', lineDashStyle: [4, 4]}
      }
    }));
    resultsPanel.add(methodNote([FORMULAS.insar,
      'Línea punteada: percentil 5 vertical = sector de máxima subsidencia ' +
      '(la media diluye cuencos localizados).',
      'Signos: vertical<0 subsidencia, >0 levantamiento; east_west>0 hacia el Este.',
      'Alerta = tasa de cambio (v reciente − v baseline), no velocidad ' +
      'absoluta: ver insar/velocity.py.']));

    Export.table.toDrive({
      collection: series, description: 'insar_roi_series',
      folder: 'geo-sentinel', fileFormat: 'CSV',
      selectors: ['date', 'vertical_mean_mm', 'east_west_mean_mm',
                  'vertical_p5_mm']
    });
    resultsPanel.add(ui.Label(
      '📥 Serie InSAR del ROI preparada en Tasks (cruzar con el Master CSV ' +
      'en analytics/balance_insar.py).', {fontSize: '11px', color: '#1a73e8'}));
  }
}

// ----------------------------------------------------------------------------
// Leyenda InSAR: gradiente rojo→blanco→azul con la convención de signos
// ----------------------------------------------------------------------------
function buildInsarLegend(rangeMm) {
  var legend = ui.Panel({style: {position: 'bottom-left', padding: '8px'}});
  legend.add(ui.Label('Desplazamiento vertical (mm)',
    {fontWeight: 'bold', fontSize: '12px'}));
  var gradient = ee.Image.pixelLonLat().select('longitude')
      .multiply((rangeMm * 2) / 100).subtract(rangeMm);
  legend.add(ui.Thumbnail({
    image: gradient,
    params: {bbox: [0, 0, 100, 8], dimensions: '200x16',
             min: -rangeMm, max: rangeMm,
             palette: ['b2182b', 'ef8a62', 'fddbc7', 'ffffff',
                       'd1e5f0', '67a9cf', '2166ac']},
    style: {stretch: 'horizontal', margin: '4px 0'}
  }));
  legend.add(ui.Panel([
    ui.Label('-' + rangeMm, {fontSize: '11px'}),
    ui.Label('0', {fontSize: '11px', margin: '0 0 0 70px'}),
    ui.Label('+' + rangeMm, {fontSize: '11px', margin: '0 0 0 62px'})
  ], ui.Panel.Layout.flow('horizontal')));
  legend.add(ui.Label('Rojo = subsidencia (hundimiento)',
    {fontSize: '11px', color: '#b2182b'}));
  legend.add(ui.Label('Azul = levantamiento',
    {fontSize: '11px', color: '#2166ac'}));
  insarLegend = legend;
  return legend;
}
