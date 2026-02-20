const canvas = document.getElementById("particle-canvas");
const ctx = canvas?.getContext("2d");
const spotlight = document.querySelector(".spotlight");
const heroCard = document.getElementById("hero-card");
const wowButton = document.getElementById("wow-button");
const statValues = document.querySelectorAll(".stat-value");

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const particleSettings = {
  count: reducedMotion ? 35 : 110,
  maxVelocity: reducedMotion ? 0.16 : 0.36,
  connectDistance: reducedMotion ? 80 : 132,
};

let width = window.innerWidth;
let height = window.innerHeight;
let pointerX = width / 2;
let pointerY = height / 2;
let particles = [];
let shootingStars = [];
let nextStarAt = 0;
let wowTimeoutId = null;

function randomBetween(min, max) {
  return Math.random() * (max - min) + min;
}

function resizeCanvas() {
  if (!canvas || !ctx) return;

  width = window.innerWidth;
  height = window.innerHeight;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}

function createParticle() {
  return {
    x: randomBetween(0, width),
    y: randomBetween(0, height),
    vx: randomBetween(-particleSettings.maxVelocity, particleSettings.maxVelocity),
    vy: randomBetween(-particleSettings.maxVelocity, particleSettings.maxVelocity),
    radius: randomBetween(0.7, 2.3),
  };
}

function createShootingStar() {
  const fromLeft = Math.random() > 0.5;
  return {
    x: fromLeft ? -80 : width + 80,
    y: randomBetween(-40, height * 0.35),
    vx: fromLeft ? randomBetween(3.2, 6.8) : randomBetween(-6.8, -3.2),
    vy: randomBetween(1.2, 2.8),
    life: 0,
    maxLife: randomBetween(22, 44),
    length: randomBetween(90, 170),
  };
}

function initParticles() {
  particles = Array.from({ length: particleSettings.count }, createParticle);
}

function drawNetwork(speedMultiplier) {
  const connectDistSq = particleSettings.connectDistance * particleSettings.connectDistance;

  for (let i = 0; i < particles.length; i += 1) {
    const a = particles[i];
    a.x += a.vx * speedMultiplier;
    a.y += a.vy * speedMultiplier;

    if (a.x < -20) a.x = width + 20;
    if (a.x > width + 20) a.x = -20;
    if (a.y < -20) a.y = height + 20;
    if (a.y > height + 20) a.y = -20;

    ctx.beginPath();
    ctx.arc(a.x, a.y, a.radius, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(180, 225, 255, 0.9)";
    ctx.fill();

    for (let j = i + 1; j < particles.length; j += 1) {
      const b = particles[j];
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const distSq = dx * dx + dy * dy;

      if (distSq < connectDistSq) {
        const dist = Math.sqrt(distSq);
        const alpha = (1 - dist / particleSettings.connectDistance) * 0.23;
        ctx.strokeStyle = `rgba(140, 210, 255, ${alpha})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }
  }
}

function drawShootingStars(speedMultiplier) {
  for (let i = shootingStars.length - 1; i >= 0; i -= 1) {
    const star = shootingStars[i];
    star.life += 1;
    star.x += star.vx * speedMultiplier;
    star.y += star.vy * speedMultiplier;

    const fade = 1 - star.life / star.maxLife;
    const trailX = star.x - star.vx * star.length * 0.1;
    const trailY = star.y - star.vy * star.length * 0.1;
    const gradient = ctx.createLinearGradient(star.x, star.y, trailX, trailY);
    gradient.addColorStop(0, `rgba(200, 245, 255, ${Math.max(fade, 0)})`);
    gradient.addColorStop(1, "rgba(200, 245, 255, 0)");

    ctx.strokeStyle = gradient;
    ctx.lineWidth = 2.4;
    ctx.beginPath();
    ctx.moveTo(star.x, star.y);
    ctx.lineTo(trailX, trailY);
    ctx.stroke();

    if (star.life > star.maxLife) {
      shootingStars.splice(i, 1);
    }
  }
}

function loop(timestamp = 0) {
  if (!canvas || !ctx) return;

  const wowEnabled = document.body.classList.contains("wow-mode");
  const speedMultiplier = wowEnabled ? 1.45 : 1;

  ctx.clearRect(0, 0, width, height);
  drawNetwork(speedMultiplier);
  drawShootingStars(speedMultiplier);

  if (!reducedMotion && timestamp > nextStarAt) {
    shootingStars.push(createShootingStar());
    const baseGap = wowEnabled ? randomBetween(360, 820) : randomBetween(760, 1800);
    nextStarAt = timestamp + baseGap;
  }

  requestAnimationFrame(loop);
}

function setSpotlightPosition(clientX, clientY) {
  const x = (clientX / width) * 100;
  const y = (clientY / height) * 100;

  pointerX = clientX;
  pointerY = clientY;

  if (spotlight) {
    spotlight.style.setProperty("--mx", `${x}%`);
    spotlight.style.setProperty("--my", `${y}%`);
  }
  document.documentElement.style.setProperty("--mx", `${x}%`);
  document.documentElement.style.setProperty("--my", `${y}%`);
}

function applyCardParallax(clientX, clientY) {
  if (!heroCard || reducedMotion) return;

  const centerX = width / 2;
  const centerY = height / 2;
  const rotateY = ((clientX - centerX) / centerX) * 5;
  const rotateX = ((centerY - clientY) / centerY) * 4;
  heroCard.style.transform = `rotateX(${rotateX}deg) rotateY(${rotateY}deg) translateZ(0)`;
}

function animateCounter(element) {
  const targetValue = Number.parseFloat(element.dataset.target || "0");
  const suffix = element.dataset.suffix || "";
  const decimals = Number.isInteger(targetValue) ? 0 : 1;
  const duration = 1500;
  const startTime = performance.now();

  function step(now) {
    const progress = Math.min((now - startTime) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const currentValue = targetValue * eased;
    const displayValue = decimals > 0 ? currentValue.toFixed(decimals) : Math.round(currentValue);
    element.textContent = `${displayValue}${suffix}`;
    if (progress < 1) requestAnimationFrame(step);
  }

  requestAnimationFrame(step);
}

function setupCounterAnimation() {
  if (!statValues.length) return;

  const statContainer = document.querySelector(".stats");
  if (!statContainer) return;

  const trigger = () => {
    statValues.forEach((item) => animateCounter(item));
  };

  if (!("IntersectionObserver" in window)) {
    trigger();
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          trigger();
          observer.disconnect();
        }
      });
    },
    { threshold: 0.28 }
  );

  observer.observe(statContainer);
}

function spawnConfettiBurst(originX, originY, pieces = 64) {
  const colors = ["#7af0ff", "#a46bff", "#57ff9f", "#ffd86e", "#ffffff"];

  for (let i = 0; i < pieces; i += 1) {
    const piece = document.createElement("span");
    const angle = randomBetween(0, Math.PI * 2);
    const distance = randomBetween(90, 290);
    const tx = Math.cos(angle) * distance;
    const ty = Math.sin(angle) * distance + randomBetween(120, 260);

    piece.className = "confetti-piece";
    piece.style.left = `${originX}px`;
    piece.style.top = `${originY}px`;
    piece.style.background = colors[i % colors.length];
    piece.style.setProperty("--tx", `${tx.toFixed(1)}px`);
    piece.style.setProperty("--ty", `${ty.toFixed(1)}px`);
    piece.style.setProperty("--rot", `${randomBetween(-540, 540).toFixed(1)}deg`);
    piece.style.animationDuration = `${randomBetween(880, 1400).toFixed(0)}ms`;
    piece.style.opacity = randomBetween(0.75, 1).toFixed(2);
    piece.style.transform = `scale(${randomBetween(0.6, 1.2).toFixed(2)})`;

    document.body.appendChild(piece);
    setTimeout(() => piece.remove(), 1450);
  }
}

function launchWowMode(originX = width / 2, originY = height * 0.42) {
  document.body.classList.add("wow-mode");
  if (wowTimeoutId) clearTimeout(wowTimeoutId);
  wowTimeoutId = setTimeout(() => {
    document.body.classList.remove("wow-mode");
  }, 2400);

  if (!reducedMotion) {
    spawnConfettiBurst(originX, originY, 74);
    shootingStars.push(createShootingStar(), createShootingStar(), createShootingStar());
  }
}

window.addEventListener("pointermove", (event) => {
  setSpotlightPosition(event.clientX, event.clientY);
  applyCardParallax(event.clientX, event.clientY);
});

window.addEventListener("resize", () => {
  width = window.innerWidth;
  height = window.innerHeight;
  resizeCanvas();
  initParticles();
});

window.addEventListener("blur", () => {
  if (heroCard) {
    heroCard.style.transform = "rotateX(0deg) rotateY(0deg) translateZ(0)";
  }
});

wowButton?.addEventListener("click", () => {
  const rect = wowButton.getBoundingClientRect();
  launchWowMode(rect.left + rect.width / 2, rect.top + rect.height / 2);
});

window.addEventListener("keydown", (event) => {
  if (event.key.toLowerCase() === "w") {
    launchWowMode(pointerX, pointerY);
  }
});

setupCounterAnimation();
resizeCanvas();
initParticles();
loop();

if (!reducedMotion) {
  setTimeout(() => launchWowMode(width / 2, height * 0.38), 850);
}
