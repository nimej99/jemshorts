const container = document.querySelector("#products");
const empty = document.querySelector("#empty");
const template = document.querySelector("#product-template");

function trackClick(product, offer) {
  window.dispatchEvent(new CustomEvent("jemshorts:product-click", {
    detail: { productId: product.id, destination: offer.id }
  }));

  const endpoint = document.documentElement.dataset.analyticsEndpoint;
  if (endpoint) {
    navigator.sendBeacon(endpoint, JSON.stringify({
      event: "product_click",
      product_id: product.id,
      merchant: offer.merchant,
      at: new Date().toISOString()
    }));
  }
}

function renderProduct(product) {
  const node = template.content.cloneNode(true);
  const image = node.querySelector(".product-image");
  image.src = product.image;
  image.alt = `${product.name} 상품 이미지`;
  node.querySelector("h2").textContent = product.name;
  node.querySelector(".details").href = `p/${product.id}.html`;
  node.querySelector(".summary").textContent = product.summary;
  const offers = (product.offers || [])
    .filter(offer => offer.active !== false)
    .sort((left, right) => left.price - right.price);
  const best = offers[0];
  node.querySelector(".price").textContent = best
    ? `최저 ${best.priceText}`
    : "판매 정보 확인 중";
  node.querySelector(".updated").textContent = `정보 확인: ${product.checkedAt}`;

  const badges = node.querySelector(".badges");
  for (const text of product.badges || []) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = text;
    badges.appendChild(badge);
  }

  const offerActions = node.querySelector(".offer-actions");
  for (const offer of offers) {
    const buy = document.createElement("a");
    buy.className = "buy";
    buy.href = offer.affiliateUrl;
    buy.rel = "sponsored nofollow noopener";
    buy.target = "_blank";
    buy.dataset.productId = product.id;
    buy.textContent = `${offer.merchant} ${offer.priceText} 확인`;
    buy.addEventListener("click", () => trackClick(product, offer));
    offerActions.appendChild(buy);
  }
  const watch = node.querySelector(".watch");
  watch.href = product.videoUrl;
  return node;
}

fetch("products.json", { cache: "no-store" })
  .then(response => {
    if (!response.ok) throw new Error(`products.json: ${response.status}`);
    return response.json();
  })
  .then(({ products }) => {
    const visible = products.filter(product => product.active !== false);
    empty.hidden = visible.length > 0;
    for (const product of visible) container.appendChild(renderProduct(product));
  })
  .catch(error => {
    empty.hidden = false;
    empty.textContent = "제품 정보를 불러오지 못했습니다.";
    console.error(error);
  });
