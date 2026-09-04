const container = document.querySelector("#products");
const collectionContainer = document.querySelector("#collections");
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

function isFresh(offer, freshnessDays) {
  const checked = new Date(`${offer.checkedAt}T23:59:59Z`);
  if (Number.isNaN(checked.getTime())) return false;
  const ageMs = Date.now() - checked.getTime();
  return ageMs <= freshnessDays * 86400000 && ageMs >= -86400000;
}

function renderProduct(product, freshnessDays) {
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
  const freshOffers = offers.filter(offer => isFresh(offer, freshnessDays));
  const displayOffers = freshOffers.length ? freshOffers : offers;
  const best = displayOffers[0];
  node.querySelector(".price").textContent = best
    ? freshOffers.length
      ? `현재 최저 ${best.priceText}`
      : `최근 확인가 ${best.priceText}`
    : "판매 정보 확인 중";
  const priceNote = node.querySelector(".price-note");
  if (!freshOffers.length && offers.length) {
    priceNote.textContent = "가격 확인일이 지나 판매처에서 최신 가격을 다시 확인하세요.";
    priceNote.className = "price-note stale";
  } else if (freshOffers.length >= 2) {
    const savings = freshOffers[1].price - freshOffers[0].price;
    priceNote.textContent = savings > 0
      ? `${best.merchant}가 다음 판매처보다 ${savings.toLocaleString("ko-KR")}원 저렴`
      : "판매처 가격 동일";
    priceNote.className = "price-note savings";
  }
  node.querySelector(".updated").textContent = best
    ? `가격 확인: ${best.checkedAt}`
    : "가격 확인 정보 없음";

  const badges = node.querySelector(".badges");
  for (const text of product.badges || []) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = text;
    badges.appendChild(badge);
  }

  const offerActions = node.querySelector(".offer-actions");
  for (const offer of displayOffers) {
    const buy = document.createElement("a");
    buy.className = "buy";
    buy.href = offer.affiliateUrl;
    buy.rel = "sponsored nofollow noopener";
    buy.target = "_blank";
    buy.dataset.productId = product.id;
    buy.textContent = freshOffers.length
      ? `${offer.merchant} ${offer.priceText} 확인`
      : `${offer.merchant} 최신 가격 다시 확인`;
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
  .then(({ products, collections = [], offerFreshnessDays = 3 }) => {
    for (const collection of collections.filter(item => item.active !== false)) {
      const link = document.createElement("a");
      link.className = "collection-link";
      link.href = `c/${collection.id}.html`;
      link.textContent = collection.title;
      collectionContainer.appendChild(link);
    }
    const visible = products.filter(product => product.active !== false);
    empty.hidden = visible.length > 0;
    for (const product of visible) {
      container.appendChild(renderProduct(product, offerFreshnessDays));
    }
  })
  .catch(error => {
    empty.hidden = false;
    empty.textContent = "제품 정보를 불러오지 못했습니다.";
    console.error(error);
  });
