#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScalpingBot v3.0 - 전략 시뮬레이터
손익 구조 및 기대값 분석
"""

def simulate_strategy(
    win_rate: float,          # 승률 (0~1)
    take_profit: float,       # 익절 (%)
    stop_loss: float,         # 손절 (%, 음수)
    slippage: float = 0.30,   # 슬리피지 (%)
    commission: float = 0.03, # 수수료 (왕복, %)
    tax: float = 0.18,        # 거래세 (%)
    daily_trades: int = 10,   # 일일 거래 횟수
    trading_days: int = 22,   # 월간 거래일
):
    """전략 시뮬레이션"""
    
    # 비용 계산
    total_cost = slippage + commission + tax
    
    # 순익/순손
    net_profit = take_profit - total_cost
    net_loss = abs(stop_loss) + total_cost
    
    # 손익비
    profit_loss_ratio = net_profit / net_loss
    
    # 손익분기 승률
    breakeven_winrate = net_loss / (net_profit + net_loss)
    
    # 일일 기대값
    daily_expected = (
        daily_trades * win_rate * net_profit -
        daily_trades * (1 - win_rate) * net_loss
    )
    
    # 월간 기대값
    monthly_expected = daily_expected * trading_days
    
    return {
        'total_cost': total_cost,
        'net_profit': net_profit,
        'net_loss': net_loss,
        'profit_loss_ratio': profit_loss_ratio,
        'breakeven_winrate': breakeven_winrate,
        'daily_expected': daily_expected,
        'monthly_expected': monthly_expected,
    }


def main():
    print("=" * 60)
    print("ScalpingBot v3.0 - 전략 시뮬레이터")
    print("=" * 60)
    
    # 현재 설정
    print("\n📊 현재 설정")
    print("-" * 40)
    print(f"익절: +1.5%")
    print(f"손절: -0.7%")
    print(f"슬리피지: 0.30%")
    print(f"수수료: 0.03% (왕복)")
    print(f"거래세: 0.18%")
    
    # 승률별 시뮬레이션
    print("\n📈 승률별 기대 수익")
    print("-" * 60)
    print(f"{'승률':>6} | {'일일':>10} | {'월간':>10} | {'판정':>8}")
    print("-" * 60)
    
    for win_rate in [0.50, 0.52, 0.55, 0.58, 0.60, 0.65, 0.70]:
        result = simulate_strategy(
            win_rate=win_rate,
            take_profit=1.5,
            stop_loss=-0.7,
        )
        
        daily = result['daily_expected']
        monthly = result['monthly_expected']
        
        if monthly > 10:
            status = "🟢 좋음"
        elif monthly > 0:
            status = "🟡 보통"
        else:
            status = "🔴 손실"
        
        print(f"{win_rate*100:>5.0f}% | {daily:>+9.2f}% | {monthly:>+9.1f}% | {status}")
    
    print("-" * 60)
    
    # 손익분기 분석
    result = simulate_strategy(
        win_rate=0.55,
        take_profit=1.5,
        stop_loss=-0.7,
    )
    
    print("\n📊 현재 설정 분석")
    print("-" * 40)
    print(f"총 비용 (왕복):  {result['total_cost']:.2f}%")
    print(f"순익 (익절-비용): +{result['net_profit']:.2f}%")
    print(f"순손 (손절+비용): -{result['net_loss']:.2f}%")
    print(f"손익비:          1:{result['profit_loss_ratio']:.2f}")
    print(f"손익분기 승률:   {result['breakeven_winrate']*100:.1f}%")
    
    # 다양한 설정 비교
    print("\n📊 설정 비교")
    print("-" * 70)
    print(f"{'익절':>6} | {'손절':>6} | {'순익':>6} | {'순손':>6} | {'손익비':>8} | {'손익분기':>8}")
    print("-" * 70)
    
    scenarios = [
        (1.0, -0.5),
        (1.5, -0.7),  # 현재
        (2.0, -1.0),
        (2.5, -1.2),
    ]
    
    for tp, sl in scenarios:
        r = simulate_strategy(win_rate=0.55, take_profit=tp, stop_loss=sl)
        marker = " ← 현재" if tp == 1.5 else ""
        print(f"+{tp:.1f}% | {sl:.1f}% | +{r['net_profit']:.2f}% | -{r['net_loss']:.2f}% | "
              f"1:{r['profit_loss_ratio']:.2f}  | {r['breakeven_winrate']*100:>6.1f}%{marker}")
    
    print("-" * 70)
    
    # 결론
    print("\n💡 결론")
    print("-" * 40)
    print("현재 설정 (익절 +1.5%, 손절 -0.7%):")
    print(f"  - 손익분기 승률: {result['breakeven_winrate']*100:.1f}%")
    print(f"  - 승률 58% 시 일일: +{simulate_strategy(0.58, 1.5, -0.7)['daily_expected']:.2f}%")
    print(f"  - 승률 58% 시 월간: +{simulate_strategy(0.58, 1.5, -0.7)['monthly_expected']:.1f}%")
    print("\n✅ 승률 55% 이상 유지가 핵심!")


if __name__ == '__main__':
    main()
