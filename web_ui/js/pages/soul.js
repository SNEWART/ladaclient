registerPage('soul', {
    title: 'Глубинное состояние',
    mount: async (el) => {
        el.innerHTML = `
            <div class="grid" id="soul-mood-card"></div>
            <div class="soul-container" id="soul-grid"></div>
        `;
        try {
            const s = await api('/api/soul');
            document.getElementById('soul-mood-card').innerHTML = `
                <div class="card" style="grid-column: 1 / -1;">
                    <div class="label">Текущее настроение</div>
                    <div class="value" style="font-size:22px">${s.mood}</div>
                </div>
            `;
            const order = [
                ["гордыня", "смирение"],
                ["жадность", "щедрость"],
                ["похоть", "целомудрие"],
                ["зависть", "доброжелательность"],
                ["чревоугодие", "умеренность"],
                ["гнев", "терпение"],
                ["уныние", "усердие"],
            ];
            document.getElementById('soul-grid').innerHTML = order.map(([sin, virt]) => {
                const sv = s.sins[sin] ?? 50;
                const vv = s.sins[virt] ?? 50;
                return `
                    <div class="soul-pair">
                        <div class="soul-row sin"><span class="name">${sin}</span><span class="val">${Math.round(sv)}</span></div>
                        <div class="soul-bar sin"><div style="width: ${sv}%"></div></div>
                        <div class="soul-row virt"><span class="name">${virt}</span><span class="val">${Math.round(vv)}</span></div>
                        <div class="soul-bar virt"><div style="width: ${vv}%"></div></div>
                    </div>
                `;
            }).join('');
        } catch (e) {
            el.innerHTML = `<div class="empty">Ошибка: ${e.message}</div>`;
        }
    },
});