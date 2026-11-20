(() => {
    const HEADER = "Idempotency-Key";
    const actions = new Map();

    function uuid() {
        if (window.crypto.randomUUID) return window.crypto.randomUUID();
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }

    function signature(formData) {
        return JSON.stringify(
            [...formData.entries()].map(([name, value]) => [
                name,
                value instanceof File ? `${value.name}:${value.size}:${value.lastModified}` : value,
            ])
        );
    }

    function keyFor(form, formData) {
        const sig = signature(formData);
        const held = actions.get(form);
        if (held && held.sig === sig) return held.key;
        const fresh = { key: uuid(), sig, waiting: new Set() };
        actions.set(form, fresh);
        return fresh.key;
    }

    function holdUntilSettled(form, key, jobIds) {
        const held = actions.get(form);
        if (!held || held.key !== key) return;
        held.waiting = new Set(jobIds);
        if (!held.waiting.size) actions.delete(form);
    }

    document.addEventListener("job:settled", (e) => {
        for (const [form, held] of actions) {
            if (held.waiting.delete(e.detail.jobId) && !held.waiting.size) actions.delete(form);
        }
    });

    const RETRIES = 4;

    async function send(post) {
        for (let attempt = 0; ; attempt += 1) {
            const xhr = await post();
            if (xhr.status !== 409 || attempt >= RETRIES) return xhr;
            const after = Number(xhr.getResponseHeader("Retry-After")) || 1;
            await new Promise((resolve) => setTimeout(resolve, Math.max(after, 2 ** attempt) * 1000));
        }
    }

    window.Idempotency = { HEADER, keyFor, holdUntilSettled, send };
})();
