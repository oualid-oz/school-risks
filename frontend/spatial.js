// "Autour d'un point": click the map or search an address, then show the catchment sector
// containing the point (PostGIS ST_Contains) and the schools nearby (ST_DWithin).
// Addresses are geocoded by the Base adresse nationale (api-adresse.data.gouv.fr), from the
// browser: the typed text goes to that public service, nothing else does.

const NEARBY_RADIUS_KM = 5;
const BAN_URL = "https://api-adresse.data.gouv.fr/search/";
// Loiret: bias results towards the department, keep only its postcodes (45xxx).
const LOIRET_CENTRE = { lat: 47.91, lon: 2.25 };

let pointMarker = null;
let pointRequest = 0; // ignore answers of a click superseded by a newer one

function setupSpatial() {
  const form = document.getElementById("address-form");
  const input = document.getElementById("address-input");
  const results = document.getElementById("address-results");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = input.value.trim();
    if (query.length < 3) return;
    results.replaceChildren(element("li", "muted", "Recherche…"));
    results.hidden = false;
    try {
      const url = `${BAN_URL}?q=${encodeURIComponent(query)}&limit=6&lat=${LOIRET_CENTRE.lat}&lon=${LOIRET_CENTRE.lon}`;
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Erreur ${response.status}`);
      const features = (await response.json()).features.filter((f) => String(f.properties.postcode || "").startsWith("45"));
      if (!features.length) {
        results.replaceChildren(element("li", "muted", "Aucune adresse trouvée dans le Loiret."));
        return;
      }
      results.replaceChildren(...features.map((feature) => {
        const item = element("li");
        const button = element("button", "", feature.properties.label);
        button.type = "button";
        button.setAttribute("role", "option");
        button.addEventListener("click", () => {
          const [lon, lat] = feature.geometry.coordinates;
          results.hidden = true;
          input.value = feature.properties.label;
          map.setView([lat, lon], 13);
          selectPoint(lat, lon, feature.properties.label);
        });
        item.append(button);
        return item;
      }));
    } catch (error) {
      results.replaceChildren(element("li", "error", `Recherche d’adresse indisponible : ${error.message}`));
    }
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") results.hidden = true;
  });
}

// Called by renderMap (app.js) once the map exists.
function enableMapPointSelection() {
  pointMarker = null;
  map.on("click", (event) => selectPoint(event.latlng.lat, event.latlng.lng));
}

function schoolLink(school, extra) {
  const button = element("button", "point-school");
  button.type = "button";
  button.append(
    element("span", `score-dot ${riskClass(school.score_global)}`, String(school.score_global)),
    element("span", "", school.nom),
    element("small", "muted", extra || school.commune)
  );
  button.addEventListener("click", () => openSchool(school.uai));
  return button;
}

async function selectPoint(lat, lon, label) {
  const request = ++pointRequest;
  const panel = document.getElementById("point-panel");
  if (pointMarker) pointMarker.setLatLng([lat, lon]);
  else {
    pointMarker = L.circleMarker([lat, lon], {
      radius: 7, color: "#17231e", weight: 3, fillColor: "#ffffff", fillOpacity: 1, interactive: false,
    }).addTo(map);
  }
  panel.hidden = false;
  const title = label || `Point ${lat.toFixed(5).replace(".", ",")} ; ${lon.toFixed(5).replace(".", ",")}`;
  panel.replaceChildren(element("h3", "", title), element("p", "muted small", "Recherche du secteur et des collèges proches…"));

  const params = `lat=${lat.toFixed(6)}&lon=${lon.toFixed(6)}`;
  let located;
  let nearby;
  try {
    [located, nearby] = await Promise.all([
      getJson(`/secteurs/localiser?${params}`),
      getJson(`/colleges/proches?${params}&rayon_km=${NEARBY_RADIUS_KM}&limit=6`),
    ]);
  } catch (error) {
    if (request !== pointRequest) return;
    const unavailable = /PostGIS/.test(error.message);
    panel.replaceChildren(
      element("h3", "", title),
      element("p", "muted small", unavailable
        ? "Cette fonction nécessite la base PostGIS (elle n’est pas disponible avec SQLite)."
        : `Impossible d’interroger ce point : ${error.message}`)
    );
    return;
  }
  if (request !== pointRequest) return;

  const sector = element("div", "point-block");
  sector.append(element("h4", "", "Secteur de collège"));
  if (located.secteur) {
    sector.append(element("p", "", `${located.secteur.secteur || located.secteur.secteur_id} (${located.secteur.commune || "—"})`));
    located.colleges.forEach((school) => sector.append(schoolLink(school, "collège de secteur")));
  } else {
    sector.append(element("p", "muted small", "Ce point n’est dans aucun secteur connu (hors du Loiret ou secteur non cartographié)."));
  }

  const near = element("div", "point-block");
  near.append(element("h4", "", `Collèges à moins de ${NEARBY_RADIUS_KM} km`));
  if (nearby.length) {
    nearby.forEach((school) => near.append(schoolLink(school, `${school.distance_km.toFixed(1).replace(".", ",")} km · ${school.commune}`)));
  } else {
    near.append(element("p", "muted small", "Aucun collège dans ce rayon."));
  }
  const close = element("button", "point-close", "Effacer le point");
  close.type = "button";
  close.addEventListener("click", clearPoint);
  panel.replaceChildren(element("h3", "", title), sector, near, close);
}

function clearPoint() {
  pointRequest += 1;
  if (pointMarker) {
    pointMarker.remove();
    pointMarker = null;
  }
  document.getElementById("point-panel").hidden = true;
}
