from __future__ import annotations

from typing import Any


def fetch_sample_news() -> list[dict[str, Any]]:
    return [
        # --- 기존 기본 ---
        {
            "id": "sample-1",
            "source": "sample",
            "headline": "NVIDIA jumps after analysts raise targets on AI demand",
            "summary": "Analysts cite stronger GPU demand and supply chain improvements.",
            "url": "",
            "datetime": "2026-03-28T12:00:00Z",
        },
        {
            "id": "sample-2",
            "source": "sample",
            "headline": "Tesla falls as regulators open probe after new incident",
            "summary": "The probe adds pressure on sentiment and delivery outlook.",
            "url": "",
            "datetime": "2026-03-28T12:01:00Z",
        },
        # --- 트럼프 / 중동 전쟁 / 원유 ---
        {
            "id": "sample-3",
            "source": "sample",
            "headline": "Trump threatens massive strikes on Iran as Middle East tensions escalate",
            "summary": "President Trump warns of military action against Iran after attacks on US bases. Oil prices surge on war fears. Defense stocks rally while tech stocks drop sharply.",
            "url": "",
            "datetime": "2026-03-28T12:02:00Z",
        },
        {
            "id": "sample-4",
            "source": "sample",
            "headline": "Oil surges past $95 as Iran-US conflict fears grip markets",
            "summary": "Crude oil jumps on Middle East war risk. Strait of Hormuz disruption fears push energy prices higher. USO and XLE rally while Nasdaq slumps.",
            "url": "",
            "datetime": "2026-03-28T12:03:00Z",
        },
        {
            "id": "sample-5",
            "source": "sample",
            "headline": "Nasdaq drops 3% as Middle East war fears slam tech stocks",
            "summary": "Apple, NVIDIA, and Microsoft fall sharply as investors flee risk assets. Semiconductor chips sector hit hard. TQQQ and SOXL plunge on escalating Iran tensions.",
            "url": "",
            "datetime": "2026-03-28T12:04:00Z",
        },
        {
            "id": "sample-6",
            "source": "sample",
            "headline": "Trump signs executive order banning Iranian oil imports",
            "summary": "The ban cuts off remaining Iranian crude supply. Oil prices surge further. Natural gas also jumps as energy complex rallies.",
            "url": "",
            "datetime": "2026-03-28T12:05:00Z",
        },
        # --- TQQQ / SOXL 반도체 ---
        {
            "id": "sample-7",
            "source": "sample",
            "headline": "Semiconductor stocks crash as new US chip export ban hits China sales",
            "summary": "SOXL drops 8% as chip restrictions widen. NVIDIA and AMD warn of revenue cuts from lost China business. TQQQ falls on Nasdaq weakness.",
            "url": "",
            "datetime": "2026-03-28T12:06:00Z",
        },
        {
            "id": "sample-8",
            "source": "sample",
            "headline": "SOXL surges as TSMC beats earnings and raises guidance on AI chip demand",
            "summary": "Semiconductor sector jumps on record GPU orders. AI chip demand growth accelerates. TQQQ rallies as Nasdaq recovers.",
            "url": "",
            "datetime": "2026-03-28T12:07:00Z",
        },
        # --- USO / UNG / BOIL 에너지 ---
        {
            "id": "sample-9",
            "source": "sample",
            "headline": "Crude oil jumps 5% after OPEC cuts production deeper than expected",
            "summary": "USO surges as supply tightens. Oil rally fuels energy sector gains.",
            "url": "",
            "datetime": "2026-03-28T12:08:00Z",
        },
        {
            "id": "sample-10",
            "source": "sample",
            "headline": "Natural gas surges on extreme winter forecast and LNG export boom",
            "summary": "UNG and BOIL jump as gas prices spike. Cold weather outlook drives demand higher.",
            "url": "",
            "datetime": "2026-03-28T12:09:00Z",
        },
        {
            "id": "sample-11",
            "source": "sample",
            "headline": "Oil drops sharply as Iran-US ceasefire deal reached",
            "summary": "Crude oil falls on peace hopes. USO and BOIL drop as energy rally reverses. Risk assets recover.",
            "url": "",
            "datetime": "2026-03-28T12:10:00Z",
        },
        # --- NORMAL regime 테스트 (EARNINGS, 강하지만 FEAR 아님) ---
        {
            "id": "sample-13",
            "source": "sample",
            "headline": "NVIDIA beats earnings and raises guidance on strong data center growth",
            "summary": "Revenue beats estimates. NVIDIA upgrades full year outlook.",
            "url": "",
            "datetime": "2026-03-28T12:12:00Z",
        },
        # --- 방산 ---
        {
            "id": "sample-12",
            "source": "sample",
            "headline": "Defense stocks surge as Pentagon announces emergency spending",
            "summary": "Lockheed Martin and Raytheon jump on massive new defense contracts. Military buildup in Middle East accelerates.",
            "url": "",
            "datetime": "2026-03-28T12:11:00Z",
        },
        # --- 축5: THEME (테마/신기술) ---
        {
            "id": "sample-14",
            "source": "sample",
            "headline": "Generative AI infrastructure spending surges as hyperscalers race to build AI data centers",
            "summary": "Large language model demand drives massive GPU orders. AI chip shortages worsen as OpenAI and Microsoft expand AI infrastructure. NVIDIA and AMD benefit.",
            "url": "",
            "datetime": "2026-03-28T12:13:00Z",
        },
        {
            "id": "sample-15",
            "source": "sample",
            "headline": "Quantum computing breakthrough: new quantum chip achieves 1000 qubit milestone",
            "summary": "IonQ and Rigetti surge on quantum supremacy claims. Quantum computing stocks rally as practical applications draw closer.",
            "url": "",
            "datetime": "2026-03-28T12:14:00Z",
        },
        {
            "id": "sample-16",
            "source": "sample",
            "headline": "Nuclear power stocks jump as US approves three small modular reactor sites",
            "summary": "Uranium prices surge. Cameco and UEC rally on SMR expansion plans. Nuclear power renaissance gains momentum as AI data center power demand soars.",
            "url": "",
            "datetime": "2026-03-28T12:15:00Z",
        },
        {
            "id": "sample-17",
            "source": "sample",
            "headline": "Tesla robot Optimus enters mass production as humanoid robotics race heats up",
            "summary": "Tesla Bot Optimus production ramps up. Robotics stocks surge on automation demand. Figure AI and Boston Dynamics announce competing humanoid robots.",
            "url": "",
            "datetime": "2026-03-28T12:16:00Z",
        },
        {
            "id": "sample-18",
            "source": "sample",
            "headline": "FDA approves breakthrough weight loss drug, biotech sector surges",
            "summary": "New GLP-1 drug approval sends pharma stocks higher. Ozempic competitor shows superior clinical trial results.",
            "url": "",
            "datetime": "2026-03-28T12:17:00Z",
        },
    ]
