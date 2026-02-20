const canvas = document.getElementById("particle-canvas");
const ctx = canvas.getContext("2d");
const spotlight = document.querySelector(".spotlight");
const heroCard = document.getElementById("hero-card");

const particleSettings = {
  count: 95,
  maxVelocity: 0.35,
  connectDistance: 130,
};

let width = window.innerWidth;
let height = window.innerHeight;
let particles = [];

function resizeCanvas() {
  width = window.innerWidth;
  height = window.innerHeight;
  canvas.width = width * window.devicePixelRatio;
  canvas.height = height * window.devicePixelRatio;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  ctx.setTransform(window.devicePixelRatio, 0, 0, window.devicePixelRatio, 0, 0);
}

function randomBetween(min, max) {
  return Math.random() * (max - min) + min;
}

function createParticle() {
  return {
    x: randomBetween(0, width),
    y: randomBetween(0, height),
    vx: randomBetween(-particleSettings.maxVelocity, particleSettings.maxVelocity),
    vy: randomBetween(-particleSettings.maxVelocity, particleSettings.maxVelocity),
    r: randomBetween(0.7, 2.6),
  };
}

function initParticles() {
  particles = Array.from({ length: particleSettings.count }, createParticle);
}

function drawParticles() {
  ctx.clearRect(0, 0, width, height);

  for (let i = 0; i < particles.length; i += 1) {
    const a = particles[i];
    a.x += a.vx;
    a.y += a.vy;

    if (a.x < -20) a.x = width + 20;
    if (a.x > width + 20) a.x = -20;
    if (a.y < -20) a.y = height + 20;
    if (a.y > height + 20) a.y = -20;

    ctx.beginPath();
    ctx.arc(a.x, a.y, a.r, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(180, 225, 255, 0.9)";
    ctx.fill();

    for (let j = i + 1; j < particles.length; j += 1) {
      const b = particles[j];
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      if (dist < particleSettings.connectDistance) {
        const alpha = (1 - dist / particleSettings.connectDistance) * 0.22;
        ctx.strokeStyle = `rgba(140, 210, 255, ${alpha})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }
  }

  requestAnimationFrame(drawParticles);
}

function setSpotlightPosition(clientX, clientY) {
  const x = (clientX / width) * 100;
  const y = (clientY / height) * 100;

  spotlight.style.setProperty("--mx", `${x}%`);
  spotlight.style.setProperty("--my", `${y}%`);
  document.documentElement.style.setProperty("--mx", `${x}%`);
  document.documentElement.style.setProperty("--my", `${y}%`);
}

function applyCardParallax(clientX, clientY) {
  if (!heroCard) return;

  const centerX = width / 2;
  const centerY = height / 2;
  const rotateY = ((clientX - centerX) / centerX) * 5;
  const rotateX = ((centerY - clientY) / centerY) * 4;
  heroCard.style.transform = `rotateX(${rotateX}deg) rotateY(${rotateY}deg) translateZ(0)`;
}

window.addEventListener("pointermove", (event) => {
  setSpotlightPosition(event.clientX, event.clientY);
  applyCardParallax(event.clientX, event.clientY);
});

window.addEventListener("resize", () => {
  resizeCanvas();
  initParticles();
});

window.addEventListener("blur", () => {
  if (heroCard) {
    heroCard.style.transform = "rotateX(0deg) rotateY(0deg) translateZ(0)";
  }
});

resizeCanvas();
initParticles();
drawParticles();
