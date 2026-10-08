from __future__ import annotations
from asyncio import run
from ._global import *
from .utils import roundc
from .tempprocessing import CropStressParametersSoilSalinity, GrowingDegreeDays, SumCalendarDays
from .kinds import dp, intEnum
import math
import sys


whichtheta_AtSat = 0
# index of AtSat in whichtheta enumerated type
whichtheta_AtFC = 1
# index of AtFC in whichtheta enumerated type
whichtheta_AtWP = 2
# index of AtWP in whichtheta enumerated type
whichtheta_AtAct = 3
# index of AtAct in whichtheta enumerated type
control_begin_day = 0
# index of beginday in control enumerated type
control_end_day = 1
# index of endday in control enumerated type

def GetCDCadjustedNoStressNew(CCx, CDC, CCxAdjusted):
    CDCadjusted = 0.0

    CDCadjusted = CDC * ((CCxAdjusted + 2.29) / (CCx + 2.29))
    return CDCadjusted

def AdjustpLeafToETo(EToMean, pLeafULAct, pLeafLLAct):
    pLeafLLAct = GetCrop_pLeafDefLL()
    pLeafULAct = GetCrop_pLeafDefUL()
    if GetCrop_pMethod() == pMethod_FAOCorrection:
        pLeafLLAct = GetCrop_pLeafDefLL() + GetSimulParam_pAdjFAO()* 0.04 *(5.-EToMean)*math.log10(10.-9.*GetCrop_pLeafDefLL())
        if pLeafLLAct > 1.0:
            pLeafLLAct = 1.0
        if pLeafLLAct < 0:
            pLeafLLAct = 0.
        pLeafULAct = GetCrop_pLeafDefUL() + GetSimulParam_pAdjFAO()* 0.04 *(5.-EToMean)*math.log10(10.-9.*GetCrop_pLeafDefUL())
        if pLeafULAct > 1.0:
            pLeafULAct = 1.0
        if pLeafULAct < 0:
            pLeafULAct = 0.
    return (pLeafULAct, pLeafLLAct)


def DeterminePotentialBiomass(VirtualTimeCC, SumGDDadjCC, CO2i, GDDayi,
                              CCxWitheredTpotNoS, BiomassUnlim):

    # potential biomass - unlimited soil fertiltiy
    # 1. - CCi
    CCiPot = CanopyCoverNoStressSF(
        (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
        GetCrop_DaysToGermination(),
        GetCrop_DaysToSenescence(),
        GetCrop_DaysToHarvest(),
        GetCrop_GDDaysToGermination(),
        GetCrop_GDDaysToSenescence(),
        GetCrop_GDDaysToHarvest(),
        GetCrop_CCo(),
        GetCrop_CCx(),
        GetCrop_CGC(),
        GetCrop_CDC(),
        GetCrop_GDDCGC(),
        GetCrop_GDDCDC(),
        SumGDDadjCC,
        GetCrop_ModeCycle(),
        0,
        0
    )

    if CCiPot < 0.0:
        CCiPot = 0.0

    if CCiPot > CCxWitheredTpotNoS:
        CCxWitheredTpotNoS = CCiPot

    # 2. - Calculation of Tpot
    if GetCrop_ModeCycle() == ModeCycle_CalendarDays:
        DAP = VirtualTimeCC
    else:
        # growing degree days
        Tmin_local = GetSimulParam_Tmin()
        Tmax_local = GetSimulParam_Tmax()
        DAP = SumCalendarDays(
            roundc(SumGDDadjCC, mold=1),
            GetCrop_Day1(),
            GetCrop_Tbase(),
            GetCrop_Tupper(),
            Tmin_local,
            Tmax_local
        )
        DAP = DAP + GetSimulation_DelayedDays()  # are not considered when working with GDDays

    TpotForB = 0.0
    EpotTotForB = 0.0

    res = CalculateETpot(
        DAP,
        GetCrop_DaysToGermination(),
        GetCrop_DaysToFullCanopy(),
        GetCrop_DaysToSenescence(),
        GetCrop_DaysToHarvest(),
        0,
        CCiPot,
        GetETo(),
        GetCrop_KcTop(),
        GetCrop_KcDeclineCumul(),
        GetCrop_CCx(),
        CCxWitheredTpotNoS,
        float(GetCrop_CCEffectEvapLate()),
        CO2i,
        GDDayi,
        GetCrop_GDtranspLow(),
        TpotForB,
        EpotTotForB
    )

    if isinstance(res, tuple):
        if len(res) >= 1:
            TpotForB = res[0]
        if len(res) >= 2:
            EpotTotForB = res[1]

    # 3. - WPi for that day
    # 3a - given WPi
    WPi = GetCrop_WP() / 100.0

    # 3b - WPi decline in reproductive stage  (works with calendar days)
    if (((GetCrop_subkind() == subkind_Grain) or (GetCrop_subkind() == subkind_Tuber))
        and (GetCrop_WPy() < 100.0)
        and (GetCrop_dHIdt() > 0.0)
        and (VirtualTimeCC >= GetCrop_DaysToFlowering())):

        # WPi in reproductive stage
        fSwitch = 1.0
        DaysYieldFormation = roundc(GetCrop_HI() / GetCrop_dHIdt(), mold=1)
        DayiAfterFlowering = VirtualTimeCC - GetCrop_DaysToFlowering()

        if (DaysYieldFormation > 0) and (DayiAfterFlowering < (DaysYieldFormation / 3.0)):
            fSwitch = DayiAfterFlowering / (DaysYieldFormation / 3.0)

        WPi = WPi * (1.0 - (1.0 - GetCrop_WPy() / 100.0) * fSwitch)

    # 3c - adjustment WPi for CO2
    if roundc(100.0 * CO2i, mold=1) != roundc(100.0 * CO2Ref, mold=1):
        WPi = WPi * fAdjustedForCO2(CO2i, GetCrop_WP(), GetCrop_AdaptedToCO2())

    # 4. - Potential Biomass
    if GetETo() > 0.0:
        BiomassUnlim = BiomassUnlim + WPi * TpotForB / float(GetETo())  # ton/ha

    return CCxWitheredTpotNoS, BiomassUnlim


def DetermineBiomassAndYield(dayi, ETo, TminOnDay, TmaxOnDay, CO2i,
                             GDDayi, Tact, SumKcTop, CGCref, GDDCGCref,
                             Coeffb0, Coeffb1, Coeffb2, FracBiomassPotSF,
                             Coeffb0Salt, Coeffb1Salt, Coeffb2Salt,
                             AverageSaltStress, SumGDDadjCC, CCtot,
                             FracAssim, VirtualTimeCC, SumInterval,
                             Biomass, BiomassPot, BiomassUnlim,
                             BiomassTot, YieldPart, WPi, HItimesBEF,
                             ScorAT1, ScorAT2, HItimesAT1, HItimesAT2,
                             HItimesAT, alfa, alfaMax, SumKcTopStress,
                             SumKci,
                             WeedRCi, CCw, Trw, StressSFadjNEW,
                             PreviousStressLevel, StoreAssimilates,
                             MobilizeAssimilates, AssimToMobilize,
                             AssimMobilized, Bin, Bout, TESTVAL):

    TempRange = 5.0
    k = 2.0

    RatioBM = 0.0
    RBM = 0.0
    HItimesTotal = 0.0
    pLeafULAct = 0.0
    pLeafLLAct = 0.0
    pStomatULAct = 0.0
    pLL = 0.0
    Ksleaf = 0.0
    Ksstomatal = 0.0
    KsPolWS = 0.0
    KsPolCs = 0.0
    KsPolHs = 0.0
    KsPol = 0.0
    Wrel = 0.0
    Dcor = 0.0
    fFlor = 0.0
    fSwitch = 0.0
    fCCx = 0.0
    WPsf = 0.0
    WPunlim = 0.0
    BioAdj = 0.0
    CCtotStar = 0.0
    CCwStar = 0.0
    croppol_temp = 0.0
    tmax1 = 0
    tmax2 = 0
    DayCor = 0
    DayiAfterFlowering = 0
    DaysYieldFormation = 0
    wdrc_temp = 0
    HIfinal_temp = 0
    PercentLagPhase = 0
    SWCtopSoilConsidered_temp = False
    TESTVAL = undef_int

    # 0. Reference HarvestIndex for that day (alfa in percentage) + Information on PercentLagPhase (for estimate WPi)
    if ((GetCrop_subkind() == subkind_Tuber) or (GetCrop_subkind() == subkind_Grain)
        or (GetCrop_subkind() == subkind_Vegetative)
        or (GetCrop_subkind() == subkind_Forage)):
        # DaysToFlowering corresponds with Tuberformation
        if (((GetCrop_subkind() == subkind_Vegetative) and (GetCrop_Planting() == plant_regrowth))
            or ((GetCrop_subkind() == subkind_Forage) and (GetCrop_Planting() == plant_regrowth))):
            alfa = GetCrop_HI()
        else:
            HIfinal_temp = GetSimulation_HIfinal()
            alfa, PercentLagPhase, HIfinal_temp = HarvestIndexDay(
                (dayi - GetCrop_Day1()),
                GetCrop_DaysToFlowering(),
                GetCrop_HI(),
                GetCrop_dHIdt(),
                GetCCiActual(),
                GetCrop_CCxAdjusted(),
                GetCrop_CCxWithered(),
                GetSimulParam_PercCCxHIfinal(),
                GetCrop_Planting(),
                PercentLagPhase,
                HIfinal_temp
            )
            SetSimulation_HIfinal(HIfinal_temp)

    WPi = (GetCrop_WP() / 100.0)

    # 1. biomass
    if ETo > 0.0:
        # 1.1 WPi for that day
        # 1.1a - given WPi
        WPi = (GetCrop_WP() / 100.0)

        # 1.1b - adjustment WPi for reproductive stage (works with calendar days)
        if (((GetCrop_subkind() == subkind_Tuber) or (GetCrop_subkind() == subkind_Grain)) and (alfa > 0.0)):
            # WPi switch to WP for reproductive stage
            fSwitch = 1.0
            DaysYieldFormation = roundc(GetCrop_HI() / GetCrop_dHIdt(), mold=1)
            if DaysYieldFormation > 0:
                if GetCrop_DeterminancyLinked():
                    fSwitch = PercentLagPhase / 100.0
                else:
                    DayiAfterFlowering = dayi - GetSimulation_DelayedDays() - GetCrop_Day1() - GetCrop_DaysToFlowering()
                    if DayiAfterFlowering < (DaysYieldFormation / 3.0):
                        fSwitch = DayiAfterFlowering / (DaysYieldFormation / 3.0)
            WPi = WPi * (1.0 - (1.0 - GetCrop_WPy() / 100.0) * fSwitch)  # switch in Lag Phase

        # 1.1c - adjustment WPi for CO2
        if roundc(100.0 * CO2i, mold=1) != roundc(100.0 * CO2Ref, mold=1):
            WPi = WPi * fAdjustedForCO2(CO2i, GetCrop_WP(), GetCrop_AdaptedToCO2())

        # 1.1d - adjustment WPi for Soil Fertility
        WPsf = WPi          # no water stress, but fertility stress
        WPunlim = WPi       # no water stress, no fertiltiy stress
        if GetSimulation_EffectStress_RedWP() > 0.0:  # Reductions are zero if no fertility stress
            # water stress and fertility stress
            if (SumKci / SumKcTopStress) < 1.0:
                if ETo > 0.0:
                    SumKci = SumKci + Tact / ETo
                if SumKci > 0.0:
                    WPi = WPi * (1.0 - (GetSimulation_EffectStress_RedWP() / 100.0)
                                 * math.exp(k * math.log(SumKci / SumKcTopStress)))
            else:
                WPi = WPi * (1.0 - GetSimulation_EffectStress_RedWP() / 100.0)
        elif ETo > 0.0:
            SumKci = SumKci + Tact / ETo


        # 1.2 actual biomass
        if (GetSimulation_RCadj() > 0.0) and (roundc(CCtot * 10000.0, mold=1) > 0.0):
            # weed infestation
            # green canopy cover of the crop in weed-infested field
            if GetManagement_WeedDeltaRC() != 0:
                if GetCrop_subkind() == subkind_Forage:
                    fCCx = MultiplierCCxSelfThinning(
                        int(GetSimulation_YearSeason()),
                        int(GetCrop_YearCCx()),
                        GetCrop_CCxRoot()
                    )
                else:
                    fCCx = 1.0
                wdrc_temp = GetManagement_WeedDeltaRC()
                WeedRCi, wdrc_temp = GetWeedRC(
                    VirtualTimeCC, SumGDDadjCC, fCCx,
                    GetSimulation_RCadj(), GetManagement_WeedAdj(), wdrc_temp,
                    GetCrop_DaysToFullCanopySF(), GetCrop_DaysToSenescence(),
                    GetCrop_GDDaysToFullCanopySF(), GetCrop_GDDaysToSenescence(),
                    GetCrop_ModeCycle()
                )
                SetManagement_WeedDeltaRC(wdrc_temp)
            else:
                WeedRCi = GetSimulation_RCadj()

            CCw = CCtot * (1.0 - WeedRCi / 100.0)

            # correction for micro-advection
            CCtotStar = 1.72 * CCtot - 1.0 * (CCtot * CCtot) + 0.30 * (CCtot * CCtot * CCtot)
            if CCtotStar < 0.0:
                CCtotStar = 0.0
            if CCtotStar > 1.0:
                CCtotStar = 1.0

            if CCw > 0.0001:
                CCwStar = CCw + (CCtotStar - CCtot)
            else:
                CCwStar = 0.0

            # crop transpiration in weed-infested field
            if CCtotStar <= 0.0001:
                Trw = 0.0
            else:
                Trw = Tact * (CCwStar / CCtotStar)

            # crop biomass in weed-infested field
            Biomass = Biomass + WPi * (Trw / ETo)  # ton/ha
        else:
            WeedRCi = 0.0
            CCw = CCtot
            Trw = Tact
            Biomass = Biomass + WPi * (Tact / ETo)  # ton/ha

        # Transfer of assimilates
        if GetCrop_subkind() == subkind_Forage:
            # only for perennial herbaceous forage crops
            # 1. Mobilize assimilates at start of season
            if MobilizeAssimilates == True:
                # mass to mobilize
                Bin = FracAssim * WPi * (Trw / ETo)  # ton/ha
                if (AssimMobilized + Bin) > AssimToMobilize:
                    Bin = AssimToMobilize - AssimMobilized

                # cumulative mass mobilized
                AssimMobilized = AssimMobilized + Bin

                # switch mobilize off when all mass is transfered
                if roundc(1000.0 * AssimToMobilize, mold=1) <= roundc(1000.0 * AssimMobilized, mold=1):
                    MobilizeAssimilates = False

            # 2. Store assimilates at end of season
            if StoreAssimilates == True:
                # mass to store
                Bout = FracAssim * WPi * (Trw / ETo)  # ton/ha
                # cumulative mass stored
                SetSimulation_Storage_Btotal(GetSimulation_Storage_Btotal() + Bout)

            TESTVAL = FracAssim

        Biomass = Biomass + Bin - Bout  # ton/ha ! correction for transferred assimilates

        # actual total biomass (crop and weeds)
        BiomassTot = BiomassTot + WPi * (Tact / ETo)  # ton/ha  for dynamic adjustment of soil fertility stress
        BiomassTot = BiomassTot + Bin - Bout  # correction for transferred assimilates

        # 1.3 potential biomass - unlimited soil fertiltiy
        BiomassUnlim = BiomassUnlim + Bin - Bout  # correction for transferred assimilates

    # 1.4 potential biomass for given soil fertility
    BiomassPot = FracBiomassPotSF * BiomassUnlim  # ton/ha

    # 2. yield
    tmax1 = undef_int
    if ((GetCrop_subkind() == subkind_Tuber) or (GetCrop_subkind() == subkind_Grain)):
        # DaysToFlowering corresponds with Tuberformation
        if dayi > (GetSimulation_DelayedDays() + GetCrop_Day1() + GetCrop_DaysToFlowering()):
            # calculation starts when flowering has started

            # 2.2 determine HImultiplier at the start of flowering
            # effect of water stress before flowering (HItimesBEF)
            if HItimesBEF < -0.1:
                # i.e. undefined at the start of flowering
                if BiomassPot < 0.0001:
                    HItimesBEF = 1.0
                else:
                    RatioBM = Biomass / BiomassPot
                    # Not correct if weed infestation and no fertility stress
                    # for that case BiomassPot might be larger (but cannot be calculated since WP is unknown)
                    if RatioBM > 1.0:
                        RatioBM = 1.0
                    RBM = BMRange(int(GetCrop_HIincrease()))
                    HItimesBEF = HImultiplier(RatioBM, RBM, GetCrop_HIincrease())

                if GetCCiActual() <= 0.01:
                    if (GetCrop_CCxWithered() > 0.0) and (GetCCiActual() < GetCrop_CCxWithered()):
                        HItimesBEF = 0.0  # no green canopy cover left at start of flowering;
                    else:
                        HItimesBEF = 1.0

            # 2.3 Relative water content for that day
            SWCtopSoilConsidered_temp = GetSimulation_SWCtopSoilConsidered()
            SWCtopSoilConsidered_temp = DetermineRootZoneWC(GetRootingDepth(), SWCtopSoilConsidered_temp)
            SetSimulation_SWCtopSoilConsidered(SWCtopSoilConsidered_temp)
            if GetSimulation_SWCtopSoilConsidered() == True:  # top soil is relative wetter than total root zone
                Wrel = (GetRootZoneWC_ZtopFC() - GetRootZoneWC_ZtopAct()) / (GetRootZoneWC_ZtopFC() - GetRootZoneWC_ZtopWP())  # top soil
            else:
                Wrel = (GetRootZoneWC_FC() - GetRootZoneWC_Actual()) / (GetRootZoneWC_FC() - GetRootZoneWC_WP())  # total root zone

            # 2.4 Failure of Pollination during flowering (alfaMax in percentage)
            if GetCrop_subkind() == subkind_Grain:  # - only valid for fruit/grain crops (flowers)
                if ((dayi <= (GetSimulation_DelayedDays() + GetCrop_Day1() + GetCrop_DaysToFlowering() + GetCrop_LengthFlowering()))
                    and ((GetCCiActual() * 100.0) > GetSimulParam_PercCCxHIfinal())):
                    # sufficient green canopy remains

                    # 2.4a - Fraction of flowers which are flowering on day  (fFlor)
                    fFlor = FractionFlowering(dayi)

                    # 2.4b - Ks(pollination) water stress
                    pLL = 1.0
                    croppol_temp = GetCrop_pPollination()
                    KsPolWS = KsAny(Wrel, croppol_temp, pLL, 0.0)

                    # 2.4c - Ks(pollination) cold stress
                    KsPolCs = KsTemperature((GetCrop_Tcold() - TempRange), float(GetCrop_Tcold()), TminOnDay)

                    # 2.4d - Ks(pollination) heat stress
                    KsPolHs = KsTemperature((GetCrop_Theat() + TempRange), float(GetCrop_Theat()), TmaxOnDay)

                    # 2.4e - Adjust alfa
                    KsPol = KsPolWS
                    if KsPol > KsPolCs:
                        KsPol = KsPolCs
                    if KsPol > KsPolHs:
                        KsPol = KsPolHs

                    alfaMax = alfaMax + (KsPol * (1 + GetCrop_fExcess() / 100.0) * fFlor * GetCrop_HI())
                    if alfaMax > GetCrop_HI():
                        alfaMax = GetCrop_HI()
            else:
                alfaMax = GetCrop_HI()  # for Tuber crops (no flowering)

            # 2.5 determine effect of water stress affecting leaf expansion after flowering
            # from start flowering till end of determinancy
            if GetCrop_DeterminancyLinked():
                tmax1 = roundc(GetCrop_LengthFlowering() / 2.0, mold=1)
            else:
                tmax1 = (GetCrop_DaysToSenescence() - GetCrop_DaysToFlowering())

            if ((HItimesBEF > 0.99)
                and (dayi <= (GetSimulation_DelayedDays() + GetCrop_Day1() + GetCrop_DaysToFlowering() + tmax1))
                and (tmax1 > 0)
                and (roundc(GetCrop_aCoeff(), mold=1) != undef_int)
                and (GetCCiActual() > 0.001)):
                # determine KsLeaf
                pLeafULAct, pLeafLLAct = AdjustpLeafToETo(ETo, pLeafULAct, pLeafLLAct)
                Ksleaf = KsAny(Wrel, pLeafULAct, pLeafLLAct, GetCrop_KsShapeFactorLeaf())

                # daily correction
                Dcor = (1.0 + (1.0 - Ksleaf) / GetCrop_aCoeff())

                # weighted correction
                ScorAT1 = ScorAT1 + Dcor / tmax1
                DayCor = dayi - (GetSimulation_DelayedDays() + GetCrop_Day1() + GetCrop_DaysToFlowering())
                HItimesAT1 = (tmax1 * 1.0 / DayCor) * ScorAT1

            # 2.6 determine effect of water stress affecting stomatal closure after flowering
            # during yield formation
            if GetCrop_dHIdt() > 99.0:
                tmax2 = 0
            else:
                tmax2 = roundc(GetCrop_HI() / GetCrop_dHIdt(), mold=1)

            if ((HItimesBEF > 0.99)
                and (dayi <= (GetSimulation_DelayedDays() + GetCrop_Day1() + GetCrop_DaysToFlowering() + tmax2))
                and (tmax2 > 0)
                and (roundc(GetCrop_bCoeff(), mold=1) != undef_int)
                and (GetCCiActual() > 0.001)):
                # determine KsStomatal
                pStomatULAct = AdjustpStomatalToETo(ETo, pStomatULAct)
                pLL = 1.0
                Ksstomatal = KsAny(Wrel, pStomatULAct, pLL, GetCrop_KsShapeFactorStomata())

                # daily correction
                if Ksstomatal > 0.001:
                    Dcor = (math.exp(0.10 * math.log(Ksstomatal))) * (1.0 - (1.0 - Ksstomatal) / GetCrop_bCoeff())
                else:
                    Dcor = 0.0

                # weighted correction
                ScorAT2 = ScorAT2 + Dcor / tmax2
                DayCor = dayi - (GetSimulation_DelayedDays() + GetCrop_Day1() + GetCrop_DaysToFlowering())
                HItimesAT2 = (tmax2 * 1.0 / DayCor) * ScorAT2

            # 2.7 total multiplier after flowering
            if (tmax2 == 0) and (tmax1 == 0):
                HItimesAT = 1.0
            else:
                if tmax2 == 0:
                    HItimesAT = HItimesAT1
                else:
                    if tmax1 == 0:
                        HItimesAT = HItimesAT2
                    elif tmax1 <= tmax2:
                        HItimesAT = HItimesAT2 * ((tmax1 * HItimesAT1 + (tmax2 - tmax1)) / tmax2)
                        if roundc(GetCrop_bCoeff(), mold=1) == undef_int:
                            HItimesAT = HItimesAT1
                        if roundc(GetCrop_aCoeff(), mold=1) == undef_int:
                            HItimesAT = HItimesAT2
                    else:
                        HItimesAT = HItimesAT1 * ((tmax2 * HItimesAT2 + (tmax1 - tmax2)) / tmax1)
                        if roundc(GetCrop_bCoeff(), mold=1) == undef_int:
                            HItimesAT = HItimesAT1
                        if roundc(GetCrop_aCoeff(), mold=1) == undef_int:
                            HItimesAT = HItimesAT2

            # 2.8 Limit HI to allowable maximum increase
            HItimesTotal = HItimesBEF * HItimesAT
            if HItimesTotal > (1.0 + (GetCrop_DHImax() / 100.0)):
                HItimesTotal = 1.0 + (GetCrop_DHImax() / 100.0)

            # 2.9 Yield
            if alfaMax >= alfa:
                YieldPart = Biomass * HItimesTotal * (alfa / 100.0)
            else:
                YieldPart = Biomass * HItimesTotal * (alfaMax / 100.0)

    # 2bis. yield leafy vegetable crops and forage crops
    if ((GetCrop_subkind() == subkind_Vegetative) or (GetCrop_subkind() == subkind_Forage)):
        if dayi >= (GetSimulation_DelayedDays() + GetCrop_Day1() + GetCrop_DaysToFlowering()):
            # calculation starts at crop day 1 (since days to flowering is 0)
            if roundc(100.0 * ETo, mold=1) > 0.0:
                # with correction for transferred assimilates
                if GetSimulation_RCadj() > 0.0:
                    YieldPart = YieldPart + (WPi * (Trw / ETo) + Bin - Bout) * (alfa / 100.0)
                else:
                    YieldPart = YieldPart + (WPi * (Tact / ETo) + Bin - Bout) * (alfa / 100.0)

    # 3. Dynamic adjustment of soil fertility stress
    if ((GetManagement_FertilityStress() > 0) and (BiomassUnlim > 0.001)
            and GetCrop_StressResponse_Calibrated()):
        BioAdj = 100.0 * (FracBiomassPotSF + (FracBiomassPotSF - BiomassTot / BiomassUnlim))
        if BioAdj >= 100.0:
            StressSFadjNEW = 0
        else:
            if BioAdj <= epsilon(1.0):
                StressSFadjNEW = 80
            else:
                StressSFadjNEW = roundc(
                    Coeffb0 + Coeffb1 * BioAdj + Coeffb2 * BioAdj * BioAdj,
                    mold=1
                )
                if StressSFadjNEW < 0:
                    StressSFadjNEW = GetManagement_FertilityStress()
                if StressSFadjNEW > 80:
                    StressSFadjNEW = 80

            if StressSFadjNEW > GetManagement_FertilityStress():
                StressSFadjNEW = GetManagement_FertilityStress()

        if ((GetCrop_subkind() == subkind_Grain) and GetCrop_DeterminancyLinked()
            and (dayi > (GetSimulation_DelayedDays() + GetCrop_Day1()
                         + GetCrop_DaysToFlowering() + tmax1))):
            # potential vegetation period is exceeded
            if StressSFadjNEW < PreviousStressLevel:
                StressSFadjNEW = PreviousStressLevel
            if StressSFadjNEW > GetManagement_FertilityStress():
                StressSFadjNEW = GetManagement_FertilityStress()
    else:
        if (GetManagement_FertilityStress() == 0
                or (not GetCrop_StressResponse_Calibrated())):
            # no (calibrated) soil fertility stress
            StressSFadjNEW = 0
        else:
            # BiomassUnlim is too small
            StressSFadjNEW = GetManagement_FertilityStress()

    PreviousStressLevel = StressSFadjNEW
    SumKcTopStress = (1.0 - StressSFadjNEW / 100.0) * SumKcTop

    return (
        FracAssim,
        Biomass,
        BiomassPot,
        BiomassUnlim,
        BiomassTot,
        YieldPart,
        WPi,
        HItimesBEF,
        ScorAT1,
        ScorAT2,
        HItimesAT1,
        HItimesAT2,
        HItimesAT,
        alfa,
        alfaMax,
        SumKcTopStress,
        SumKci,
        WeedRCi,
        CCw,
        Trw,
        StressSFadjNEW,
        PreviousStressLevel,
        StoreAssimilates,
        MobilizeAssimilates,
        AssimToMobilize,
        AssimMobilized,
        Bin,
        Bout,
        TESTVAL
    )

def FractionFlowering(Dayi):
    _res = 0  # result
    f1 = 0
    f2 = 0
    F = 0
    DiFlor = 0
    if GetCrop_LengthFlowering() <= 1:
        F = 1.
    else:
        DiFlor = Dayi - (GetSimulation_DelayedDays() + GetCrop_Day1() + GetCrop_DaysToFlowering())
        f2 = FractionPeriod(DiFlor)
        DiFlor = (Dayi-1) - (GetSimulation_DelayedDays() + GetCrop_Day1() + GetCrop_DaysToFlowering())
        f1 = FractionPeriod(DiFlor)
        if abs(f1-f2) < ac_zero_threshold:
            F = 0.
        else:
            F = (100. * ((f1+f2)/2.)/GetCrop_LengthFlowering())
    _res = F
    return _res

def FractionPeriod(DiFlor):
    _res = 0  # result
    fi = 0
    TimePerc = 0
    if DiFlor <= epsilon(1.):
        fi = 0.
    else:
        TimePerc = 100. * (DiFlor * 1./GetCrop_LengthFlowering())
        if TimePerc > 100.:
            fi = 1.
        else:
            fi = 0.00558 * math.exp(0.63*math.log(TimePerc)) - 0.000969 * TimePerc - 0.00383
            if fi < 0.:
                fi = 0.
    _res = fi
    return _res

def YearWeighingFactor(CropFirstDayNr):
    _res = 0  # result
    Dayi = 0
    Monthi = 0
    Yeari = 0
    DetermineDate(CropFirstDayNr, Dayi, Monthi, Yeari)
    _res = Yeari
    return _res


def AdjustpStomatalToETo(MeanETo, pStomatULAct):

    if GetCrop_pMethod() == pMethod_NoCorrection:
        pStomatULAct = GetCrop_pdef()

    elif GetCrop_pMethod() == pMethod_FAOCorrection:
        pStomatULAct = GetCrop_pdef() + GetSimulParam_pAdjFAO() * (
            (0.04 * (5.0 - MeanETo)) * math.log10(10.0 - 9.0 * GetCrop_pdef())
        )

    if pStomatULAct > 1:
        pStomatULAct = 1.0
    if pStomatULAct < 0.0:
        pStomatULAct = 0.0

    return pStomatULAct

def AdjustpSenescenceToETo(EToMean, TimeSenescence, WithBeta, pSenAct):
    pSenAct = GetCrop_pSenescence()
    if GetCrop_pMethod() == pMethod_FAOCorrection:
        pSenAct = GetCrop_pSenescence() + GetSimulParam_pAdjFAO() \
                            * 0.04 * (5.0 - EToMean) \
                            * math.log10(10.0 - 9.0 * GetCrop_pSenescence())
        if (TimeSenescence > 0.0001) and WithBeta:
            pSenAct = pSenAct * (1.0 - GetSimulParam_Beta() / 100.0)
        if pSenAct < 0.0:
            pSenAct = 0.0
        if pSenAct >= 1.0:
            pSenAct = 0.98  # otherwise senescence is not possible at WP
    return pSenAct

def CheckGermination():
    Zroot = 0.0
    WCGermination = 0.0
    SWCtopSoilConsidered_temp = False

    # total root zone is considered
    Zroot = GetCrop_RootMin()
    SWCtopSoilConsidered_temp = GetSimulation_SWCtopSoilConsidered()
    SWCtopSoilConsidered_temp = DetermineRootZoneWC(Zroot, SWCtopSoilConsidered_temp)
    SetSimulation_SWCtopSoilConsidered(SWCtopSoilConsidered_temp)
    WCGermination = GetRootZoneWC_WP() + (GetRootZoneWC_FC() - GetRootZoneWC_WP()) * (GetSimulParam_TAWGermination() / 100.0)
    if GetRootZoneWC_Actual() < WCGermination:
        SetSimulation_DelayedDays(GetSimulation_DelayedDays() + 1)
        SetSimulation_SumGDD(0.0)
    else:
        SetSimulation_Germinate(True)
        if GetCrop_Planting() == plant_Seed:
            SetSimulation_ProtectedSeedling(True)
        else:
            SetSimulation_ProtectedSeedling(False)

def calculate_transpiration(Tpot, Coeffb0Salt, Coeffb1Salt, Coeffb2Salt):
    WtoExtract = 0.0
    theta_critical = 0.0
    alfa = 0.0
    sinkMM = 0.0
    compi = 0
    layeri = 0
    pre_layer = 0
    DeltaWC = 0.0
    InetThreshold = 0.0
    TpotMAX = 0.0
    RedFact = 0.0
    RedFactECsw = 0.0
    Wrel = 0.0
    WrelSalt = 0.0
    pStomatLLAct = 0.0
    crop_pActStom_tmp = 0.0
    CompiECe = 0.0
    CompiECsw = 0.0
    CompiECswFC = 0.0
    TheSum = 0.0
    TheRatio = 0.0
    TheResidue = 0.0
    SWCtopSoilConsidered_temp = False
    Comp_temp = None
    Compi_temp = None

    SetTact(0.0)

    if Tpot > 0.0:
        # 1. maximum transpiration in actual root zone
        if GetIrriMode() == IrriMode_Inet:
            # salinity stress not considered
            TpotMAX = Tpot
        else:
            SWCtopSoilConsidered_temp = GetSimulation_SWCtopSoilConsidered()
            SWCtopSoilConsidered_temp = DetermineRootZoneWC(GetRootingDepth(), SWCtopSoilConsidered_temp)
            SetSimulation_SWCtopSoilConsidered(SWCtopSoilConsidered_temp)

            # --- 1. Effect of water stress and ECe (total rootzone)
            WrelSalt = (GetRootZoneWC_FC() - GetRootZoneWC_Actual()) / (GetRootZoneWC_FC() - GetRootZoneWC_WP())

            # --- 2. Effect of water stress
            pStomatLLAct = 1.0
            if GetSimulation_SWCtopSoilConsidered() == True:
                # top soil is relative wetter than total root zone
                if GetRootZoneWC_ZtopAct() < (0.999 * GetRootZoneWC_ZtopThresh()):
                    Wrel = (GetRootZoneWC_ZtopFC() - GetRootZoneWC_ZtopAct()) / (GetRootZoneWC_ZtopFC() - GetRootZoneWC_ZtopWP())
                    crop_pActStom_tmp = GetCrop_pActStom()
                    RedFact = (1.0 - GetSimulation_EffectStress_RedKsSto() / 100.0) * KsAny(
                        Wrel, crop_pActStom_tmp, pStomatLLAct, 0.0
                    )  # where (0.0) is linear
                else:
                    RedFact = (1.0 - GetSimulation_EffectStress_RedKsSto() / 100.0)
            else:
                # total root zone
                if GetRootZoneWC_Actual() < (0.999 * GetRootZoneWC_Thresh()):
                    Wrel = (GetRootZoneWC_FC() - GetRootZoneWC_Actual()) / (GetRootZoneWC_FC() - GetRootZoneWC_WP())
                    crop_pActStom_tmp = GetCrop_pActStom()
                    RedFact = (1.0 - GetSimulation_EffectStress_RedKsSto() / 100.0) * KsAny(
                        Wrel, crop_pActStom_tmp, pStomatLLAct, 0.0
                    )  # where (0.0) is linear
                    SetCrop_pActStom(crop_pActStom_tmp)
                else:
                    RedFact = (1.0 - GetSimulation_EffectStress_RedKsSto() / 100.0)

            if RedFact < 0.0:
                RedFact = 0.0
            if RedFact > 1.0:
                RedFact = 1.0

            # --- 3. Extra effect of ECsw (salt in total root zone is considered)
            if GetSimulation_SalinityConsidered():
                RedFactECsw = AdjustedKsStoToECsw(
                    GetCrop_ECemin(),
                    GetCrop_ECemax(),
                    GetCrop_ResponseECsw(),
                    GetRootZoneSalt_ECe(),
                    GetRootZoneSalt_ECsw(),
                    GetRootZoneSalt_ECswFC(),
                    WrelSalt,
                    Coeffb0Salt,
                    Coeffb1Salt,
                    Coeffb2Salt,
                    RedFact,
                )
            else:
                RedFactECsw = RedFact

            # --- 4. Conclusion (adjustment of TpotMAX considering Water and Salt stress)
            TpotMAX = RedFactECsw * Tpot

            # 1.b anaerobic conditions in root zone (total root zone is considered)
            RedFact = DetermineRootZoneAnaeroConditions(
                GetRootZoneWC_SAT(),
                GetRootZoneWC_Actual(),
                float(GetCrop_AnaeroPoint()),
                GetRootingDepth(),
                RedFact,
            )
            TpotMAX = RedFact * TpotMAX

        # 2. extraction of TpotMax out of the compartments
        # 2.a initial settings
        Comp_temp = GetCompartment()
        calculate_rootfraction_compartment(GetRootingDepth(), Comp_temp)
        calculate_sink_values(TpotMAX, GetRootingDepth(), Comp_temp, GetCrop())
        SetCompartment(Comp_temp)
        compi = 0
        pre_layer = 0
        TheSum = 0.0
        while True:
            compi = compi + 1
            layeri = GetCompartment_Layer(compi)
            if layeri > pre_layer:
                theta_critical = calculate_theta_critical(layeri, theta_critical)
                pre_layer = layeri

            # 2.b calculate alfa
            if GetIrriMode() == IrriMode_Inet:
                alfa = 1.0
            else:
                # effect of water stress and ECe
                if GetCompartment_theta(compi) >= theta_critical:
                    alfa = (1.0 - GetSimulation_EffectStress_RedKsSto() / 100.0)
                elif GetCompartment_theta(compi) > (GetSoilLayer_WP(layeri) / 100.0):
                    if theta_critical > (GetSoilLayer_WP(layeri) / 100.0):
                        Wrel = (GetSoilLayer_FC(layeri) / 100.0 - GetCompartment_theta(compi)) / (
                            GetSoilLayer_FC(layeri) / 100.0 - GetSoilLayer_WP(layeri) / 100.0
                        )
                        pStomatLLAct = 1.0
                        crop_pActStom_tmp = GetCrop_pActStom()
                        alfa = (1.0 - GetSimulation_EffectStress_RedKsSto() / 100.0) * KsAny(
                            Wrel,
                            crop_pActStom_tmp,
                            pStomatLLAct,
                            GetCrop_KsShapeFactorStomata(),
                        )
                        SetCrop_pActStom(crop_pActStom_tmp)
                    else:
                        alfa = (1.0 - GetSimulation_EffectStress_RedKsSto() / 100.0)
                else:
                    alfa = 0.0

                # extra effect of ECsw
                if GetSimulation_SalinityConsidered():
                    WrelSalt = (GetSoilLayer_FC(layeri) / 100.0 - GetCompartment_theta(compi)) / (
                        GetSoilLayer_FC(layeri) / 100.0 - GetSoilLayer_WP(layeri) / 100.0
                    )
                    CompiECe = ECeComp(GetCompartment_i(compi))
                    CompiECsw = ECswComp(GetCompartment_i(compi), False)
                    CompiECswFC = ECswComp(GetCompartment_i(compi), True)
                    RedFactECsw = AdjustedKsStoToECsw(
                        GetCrop_ECemin(),
                        GetCrop_ECemax(),
                        GetCrop_ResponseECsw(),
                        CompiECe,
                        CompiECsw,
                        CompiECswFC,
                        WrelSalt,
                        Coeffb0Salt,
                        Coeffb1Salt,
                        Coeffb2Salt,
                        alfa,
                    )
                else:
                    RedFactECsw = alfa
                alfa = RedFactECsw

            if GetCrop_AnaeroPoint() > 0.0:
                Compi_temp = GetCompartment_i(compi)
                alfa = Correction_Anaeroby(Compi_temp, alfa)
                SetCompartment_i(compi, Compi_temp)

            # 2.c determine maximum extration amount (mm) in current root zone
            # maximum possible (with current water stress)
            SetCompartment_SinkMajor(compi, 1000 * (alfa * GetCompartment_WFactor(compi) * GetCompartment_Smax(compi)) * GetCompartment_Thickness(compi))
            
            # Sum of maximum possible (without any water stress)
            if (GetCompartment_WFactor(compi) > 0.00001):
                TheSum = TheSum + 1000 * (GetCompartment_WFactor(compi) * GetCompartment_Smax(compi)) * GetCompartment_Thickness(compi)

            if compi == GetNrCompartments():
                break

        # 2.d Determine required extraction considering root distribution
        TheRatio = TpotMAX / TheSum

        for compi in range(1, GetNrCompartments() + 1):
            if GetCompartment_WFactor(compi) > 1.0E-5:
                SetCompartment_SinkMinor(compi, TheRatio * 1000.0 *
                    GetCompartment_WFactor(compi) * GetCompartment_Smax(compi) *
                    GetCompartment_Thickness(compi))
            else:
                SetCompartment_SinkMinor(compi, 0.0)

        # 2.e Extract water
        TheResidue = 0.0
        WtoExtract = TpotMAX

        for compi in range(1, GetNrCompartments() + 1):
            if (GetCompartment_SinkMinor(compi) > 1.0E-5) and (WtoExtract > 1.0E-5):
                if GetCompartment_SinkMinor(compi) < GetCompartment_SinkMajor(compi):
                    if (GetCompartment_SinkMinor(compi) + TheResidue) > GetCompartment_SinkMajor(compi):
                        TheResidue = TheResidue - (GetCompartment_SinkMajor(compi) - GetCompartment_SinkMinor(compi))
                        sinkMM = GetCompartment_SinkMajor(compi)
                    else:
                        sinkMM = GetCompartment_SinkMinor(compi) + TheResidue
                        TheResidue = 0.0
                else:
                    sinkMM = GetCompartment_SinkMajor(compi)
                    TheResidue = TheResidue + (GetCompartment_SinkMinor(compi) - GetCompartment_SinkMajor(compi))

                # Extract water
                WtoExtract = TpotMAX - GetTact()
                if WtoExtract < sinkMM:
                    sinkMM = WtoExtract

                SetCompartment_theta(compi, GetCompartment_theta(compi) -
                    sinkMM / (1000.0 * GetCompartment_Thickness(compi) *
                    (1.0 - GetSoilLayer_GravelVol(layeri) / 100.0)))

                WtoExtract = WtoExtract - sinkMM
                SetTact(GetTact() + sinkMM)

        # 3. add net irrigation water requirement
        if GetIrriMode() == IrriMode_Inet:
            # total root zone is considered
            SWCtopSoilConsidered_temp = GetSimulation_SWCtopSoilConsidered()
            SWCtopSoilConsidered_temp = DetermineRootZoneWC(GetRootingDepth(), SWCtopSoilConsidered_temp)
            SetSimulation_SWCtopSoilConsidered(SWCtopSoilConsidered_temp)
            InetThreshold = GetRootZoneWC_FC() - GetSimulParam_PercRAW() / 100.0 * (GetRootZoneWC_FC() - GetRootZoneWC_Thresh())
            if GetRootZoneWC_Actual() < InetThreshold:
                pre_layer = 0
                for compi in range(1, int(GetNrCompartments()) + 1):
                    layeri = GetCompartment_Layer(compi)
                    if layeri > pre_layer:
                        theta_critical = calculate_theta_critical(layeri, theta_critical)
                        InetThreshold = GetSoilLayer_FC(layeri) / 100.0 - GetSimulParam_PercRAW() / 100.0 * (
                            GetSoilLayer_FC(layeri) / 100.0 - theta_critical
                        )
                        pre_layer = layeri

                    DeltaWC = (
                        GetCompartment_WFactor(compi)
                        * (InetThreshold - GetCompartment_theta(compi))
                        * 1000.0
                        * GetCompartment_Thickness(compi)
                        * (1.0 - GetSoilLayer_GravelVol(layeri) / 100.0)
                    )

                    SetCompartment_theta(
                        compi,
                        GetCompartment_theta(compi)
                        + DeltaWC
                        / (1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(layeri) / 100.0)),
                    )
                    SetIrrigation(GetIrrigation() + DeltaWC)

def calculate_theta_critical(layeri, theta_critical):
    theta_TAW = 0.0

    theta_TAW = GetSoilLayer_FC(layeri) / 100.0 - GetSoilLayer_WP(layeri) / 100.0
    theta_critical = GetSoilLayer_FC(layeri) / 100.0 - theta_TAW * GetCrop_pActStom()

    return theta_critical

def calculate_rootfraction_compartment(RootingDepth, Compartment):
    frac_value = 0.0
    cumdepth = 0.0
    compi = 0
    i = 0

    cumdepth = 0.0
    compi = 0
    while True:
        compi = compi + 1
        cumdepth = cumdepth + Compartment[compi - 1].Thickness
        if cumdepth <= RootingDepth:
            Compartment[compi - 1].WFactor = 1
        else:
            frac_value = RootingDepth - (cumdepth - Compartment[compi - 1].Thickness)
            if frac_value > 0.0:
                Compartment[compi - 1].WFactor = frac_value / Compartment[compi - 1].Thickness
            else:
                Compartment[compi - 1].WFactor = 0.0

        if (cumdepth >= RootingDepth) or (compi == GetNrCompartments()):
            break

    for i in range(int(compi + 1), int(GetNrCompartments()) + 1):
        Compartment[i - 1].WFactor = 0.0

def calculate_sink_values(Tpot, RootingDepth, Compartment, Crop):
    sink_value = 0.0
    StopComp = 0.0
    SbotComp = 0.0
    cumdepth = 0.0
    compi = 0
    i = 0

    if GetIrriMode() == IrriMode_Inet:
        sink_value = (GetCrop_SmaxTop() + GetCrop_SmaxBot()) / 2.0
        for compi in range(1, int(GetNrCompartments()) + 1):
            Compartment[compi - 1].Smax = sink_value
    else:
        cumdepth = 0.0
        compi = 0
        SbotComp = GetCrop_SmaxTop()
        while True:
            compi = compi + 1
            StopComp = SbotComp
            cumdepth = cumdepth + Compartment[compi - 1].Thickness
            if cumdepth <= RootingDepth:
                SbotComp = GetCrop_SmaxBot() * GetSimulation_SCor() + (
                    GetCrop_SmaxTop() - GetCrop_SmaxBot() * GetSimulation_SCor()
                ) * (RootingDepth - cumdepth) / RootingDepth
            else:
                SbotComp = GetCrop_SmaxBot() * GetSimulation_SCor()
            Compartment[compi - 1].Smax = (StopComp + SbotComp) / 2.0
            if Compartment[compi - 1].Smax > 0.06:
                Compartment[compi - 1].Smax = 0.06
            if (cumdepth >= RootingDepth) or (compi == GetNrCompartments()):
                break

        for i in range(int(compi + 1), int(GetNrCompartments()) + 1):
            Compartment[i - 1].Smax = 0.0

def Correction_Anaeroby(Comp, alfa):
    alfaAN = 0.0
    ini = 0

    if (GetDaySubmerged() >= GetSimulParam_DelayLowOxygen()) and (GetCrop_AnaeroPoint() > 0.0):
        alfaAN = 0.0
    elif Comp.Theta > ((GetSoilLayer_SAT(Comp.Layer) - GetCrop_AnaeroPoint()) / 100.0):
        Comp.DayAnaero = Comp.DayAnaero + 1
        if Comp.DayAnaero >= GetSimulParam_DelayLowOxygen():
            ini = 0
            Comp.DayAnaero = GetSimulParam_DelayLowOxygen()
        else:
            ini = 1
        alfaAN = (GetSoilLayer_SAT(Comp.Layer) / 100.0 - Comp.Theta) / (GetCrop_AnaeroPoint() / 100.0)
        if alfaAN < 0.0:
            alfaAN = 0.0
        if GetSimulParam_DelayLowOxygen() > 1.0:
            alfaAN = (ini + (Comp.DayAnaero - 1.0) * alfaAN) / (ini + Comp.DayAnaero - 1.0)
    else:
        alfaAN = 1.0
        Comp.DayAnaero = 0.0

    if alfa > alfaAN:
        alfa = alfaAN

    return alfa

def DetermineRootZoneAnaeroConditions(Wsat, Wact, AnaeVol, Zr, RedFact):
    SATVol = 0.0
    ACTVol = 0.0

    RedFact = 1.0
    if (AnaeVol > 0.0) and (Zr > 0.0):
        SATVol = Wsat / (10.0 * Zr)
        ACTVol = Wact / (10.0 * Zr)
        if ACTVol > SATVol:
            ACTVol = SATVol
        if ACTVol > (SATVol - AnaeVol):
            SetSimulation_DayAnaero(GetSimulation_DayAnaero() + 1)
            if GetSimulation_DayAnaero() > GetSimulParam_DelayLowOxygen():
                SetSimulation_DayAnaero(int(GetSimulParam_DelayLowOxygen()))
            RedFact = 1.0 - (1.0 - ((SATVol - ACTVol) / AnaeVol)) * (
                (GetSimulation_DayAnaero() * 1.0) / GetSimulParam_DelayLowOxygen()
            )
        else:
            SetSimulation_DayAnaero(0)
    else:
        SetSimulation_DayAnaero(0)

    return RedFact


def surface_transpiration(Coeffb0Salt, Coeffb1Salt, Coeffb2Salt):
    Part = 0.0
    KsReduction = 0.0
    SaltSurface = 0.0
    Tact_temp = 0.0

    SetDaySubmerged(GetDaySubmerged() + 1)
    for compi in range(1, int(GetNrCompartments()) + 1):
        SetCompartment_DayAnaero(compi, GetCompartment_DayAnaero(compi) + 1)
        if GetCompartment_DayAnaero(compi) > GetSimulParam_DelayLowOxygen():
            SetCompartment_DayAnaero(compi, GetSimulParam_DelayLowOxygen())

    if GetCrop_AnaeroPoint() > 0.0:
        Part = (1.0 - GetDaySubmerged() / float(GetSimulParam_DelayLowOxygen()))
    else:
        Part = 1.0

    KsReduction = KsSalinity(
        GetSimulation_SalinityConsidered(),
        GetCrop_ECemin(),
        GetCrop_ECemax(),
        GetECstorage(),
        0.0
    )

    SaltSurface = GetSurfaceStorage() * GetECstorage() * Equiv
    if GetSurfaceStorage() > KsReduction * Part * GetTpot():
        SetSurfaceStorage(GetSurfaceStorage() - KsReduction * Part * GetTpot())
        SetTact(KsReduction * Part * GetTpot())
        # salinisation of surface storage layer
        SetECstorage(SaltSurface / (GetSurfaceStorage() * Equiv))
    else:
        SetTact(GetSurfaceStorage() - 0.1)
        SetSurfaceStorage(0.1)  # zero give error in already updated salt balance

    if GetTact() < KsReduction * Part * GetTpot():
        Tact_temp = GetTact()  # (*Protect Tact from changes in the next routine*)
        calculate_transpiration(
            (KsReduction * Part * GetTpot() - GetTact()),
            Coeffb0Salt, Coeffb1Salt, Coeffb2Salt
        )
        SetTact(Tact_temp + GetTact())

# -----------------------------------------------------------------------------
# BUDGET_module
# -----------------------------------------------------------------------------
def calculate_delta_theta(theta_in, thetaAdjFC, NrLayer):
    DeltaX = 0.0
    theta = 0.0
    theta_sat = 0.0
    theta_fc = 0.0

    theta = theta_in
    theta_sat = GetSoilLayer_SAT(NrLayer) / 100.0
    theta_fc = GetSoilLayer_FC(NrLayer) / 100.0
    if theta > theta_sat:
        theta = theta_sat
    if theta <= thetaAdjFC / 100.0:
        DeltaX = 0.0
    else:
        DeltaX = GetSoilLayer_tau(NrLayer) \
                 * (theta_sat - theta_fc) \
                 * (math.exp(theta - theta_fc) - 1.0) \
                 / (math.exp(theta_sat - theta_fc) - 1.0)
        if (theta - DeltaX) < thetaAdjFC:
            DeltaX = theta - thetaAdjFC
    return DeltaX


def calculate_theta(delta_theta, thetaAdjFC, NrLayer):
    ThetaX = 0.0
    theta_sat = 0.0
    theta_fc = 0.0
    tau = 0.0

    theta_sat = GetSoilLayer_SAT(NrLayer) / 100.0
    theta_fc = GetSoilLayer_FC(NrLayer) / 100.0
    tau = GetSoilLayer_tau(NrLayer)
    if delta_theta <= 0.0:
        ThetaX = thetaAdjFC
    elif tau > 0.0:
        ThetaX = theta_fc \
            + math.log(1.0
                  + delta_theta
                  * (math.exp(theta_sat - theta_fc) - 1.0)
                  / (tau * (theta_sat - theta_fc)))
        if ThetaX < thetaAdjFC:
            ThetaX = thetaAdjFC
    else:
        # to stop draining
        ThetaX = theta_sat + 0.1
    return ThetaX


def calculate_delta_theta_adjusted_fc(theta_in, thetaAdjFC, NrLayer):
    theta = min(theta_in, GetSoilLayer_SAT(NrLayer) / 100.0)
    theta_sat = GetSoilLayer_SAT(NrLayer) / 100.0
    theta_fc = thetaAdjFC
    if theta <= thetaAdjFC:
        return 0.0
    if abs(theta_sat - theta_fc) <= sys.float_info.epsilon:
        return 0.0
    DeltaX = GetSoilLayer_tau(NrLayer) \
             * (theta_sat - theta_fc) \
             * (math.exp(theta - theta_fc) - 1.0) \
             / (math.exp(theta_sat - theta_fc) - 1.0)
    if (theta - DeltaX) < thetaAdjFC:
        DeltaX = theta - thetaAdjFC
    return DeltaX


def calculate_theta_adjusted_fc(delta_theta, thetaAdjFC, NrLayer):
    theta_sat = GetSoilLayer_SAT(NrLayer) / 100.0
    theta_fc = thetaAdjFC
    tau = GetSoilLayer_tau(NrLayer)
    if delta_theta <= 0.0:
        return thetaAdjFC
    if (tau > 0.0) and (abs(theta_sat - theta_fc) > sys.float_info.epsilon):
        ThetaX = theta_fc + math.log(
            1.0
            + delta_theta
            * (math.exp(theta_sat - theta_fc) - 1.0)
            / (tau * (theta_sat - theta_fc))
        )
        if ThetaX < thetaAdjFC:
            ThetaX = thetaAdjFC
        return ThetaX
    return theta_sat + 0.1


def receiving_compartment_below_fcadj(compi):
    for receiving_comp in range(compi + 1, int(GetNrCompartments()) + 1):
        if GetCompartment_theta(receiving_comp) < (
            (GetCompartment_FCadj(receiving_comp) / 100.0) - sys.float_info.epsilon
        ):
            return True
    return False


def calculate_drainage():
    drainsum = 0.0
    UseAdjustedFCCurve = False
    for compi in range(1, int(GetNrCompartments()) + 1):
        # 1. Calculate drainage of compartment
        # ====================================
        layeri = GetCompartment_Layer(compi)
        UseAdjustedFCCurve = (
            (abs(GetECiAqua()) <= sys.float_info.epsilon)
            and ((GetCompartment_FCadj(compi) - GetSoilLayer_FC(layeri)) > 1.0)
            and (GetCompartment_theta(compi) >= ((GetCompartment_FCadj(compi) / 100.0) - sys.float_info.epsilon))
            and (not receiving_compartment_below_fcadj(compi))
        )
        if GetCompartment_theta(compi) > (GetCompartment_FCadj(compi) / 100.0):
            if UseAdjustedFCCurve:
                delta_theta = calculate_delta_theta_adjusted_fc(
                    GetCompartment_theta(compi),
                    (GetCompartment_FCadj(compi) / 100.0),
                    layeri
                )
            else:
                delta_theta = calculate_delta_theta(
                    GetCompartment_theta(compi),
                    (GetCompartment_FCadj(compi) / 100.0),
                    layeri
                )
        else:
            delta_theta = 0.0

        drain_comp = delta_theta * 1000.0 * GetCompartment_Thickness(compi) * (
            1.0 - GetSoilLayer_GravelVol(layeri) / 100.0
        )

        # 2. Check drainability
        # =====================
        excess = 0.0
        pre_thick = 0.0
        for i in range(1, int(compi - 1) + 1):
            pre_thick = pre_thick + GetCompartment_Thickness(i)

        drainmax = delta_theta * 1000.0 * pre_thick * (1.0 - GetSoilLayer_GravelVol(layeri) / 100.0)
        if drainsum <= drainmax:
            drainability = True
        else:
            drainability = False
        # 3. Drain compartment
        # ====================
        if drainability:
            SetCompartment_theta(compi, GetCompartment_theta(compi) - delta_theta)
            drainsum = drainsum + drain_comp
            drainsum, excess = CheckDrainsum(layeri, drainsum, excess)
        else:  # drainability == .false.
            delta_theta = drainsum / (1000.0 * pre_thick * (1.0 - GetSoilLayer_GravelVol(layeri) / 100.0))
            if UseAdjustedFCCurve:
                theta_x = calculate_theta_adjusted_fc(delta_theta, (GetCompartment_FCadj(compi) / 100.0), layeri)
            else:
                theta_x = calculate_theta(delta_theta, (GetCompartment_FCadj(compi) / 100.0), layeri)

            if theta_x <= (GetSoilLayer_SAT(layeri) / 100.0):
                SetCompartment_theta(
                    compi,
                    GetCompartment_theta(compi)
                    + drainsum / (1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(layeri) / 100.0))
                )
                if GetCompartment_theta(compi) > theta_x:
                    drainsum = (GetCompartment_theta(compi) - theta_x) * 1000.0 * GetCompartment_Thickness(compi) * (
                        1.0 - GetSoilLayer_GravelVol(layeri) / 100.0
                    )
                    if UseAdjustedFCCurve:
                        delta_theta = calculate_delta_theta_adjusted_fc(theta_x, (GetCompartment_FCadj(compi) / 100.0), layeri)
                    else:
                        delta_theta = calculate_delta_theta(theta_x, (GetCompartment_FCadj(compi) / 100.0), layeri)
                    drainsum = drainsum + delta_theta * 1000.0 * GetCompartment_Thickness(compi) * (
                        1.0 - GetSoilLayer_GravelVol(layeri) / 100.0
                    )
                    drainsum, excess = CheckDrainsum(layeri, drainsum, excess)
                    SetCompartment_theta(compi, theta_x - delta_theta)
                elif GetCompartment_theta(compi) > (GetCompartment_FCadj(compi) / 100.0):
                    if UseAdjustedFCCurve:
                        delta_theta = calculate_delta_theta_adjusted_fc(
                            GetCompartment_theta(compi),
                            (GetCompartment_FCadj(compi) / 100.0),
                            layeri
                        )
                    else:
                        delta_theta = calculate_delta_theta(
                            GetCompartment_theta(compi),
                            (GetCompartment_FCadj(compi) / 100.0),
                            layeri
                        )
                    SetCompartment_theta(compi, GetCompartment_theta(compi) - delta_theta)
                    drainsum = delta_theta * 1000.0 * GetCompartment_Thickness(compi) * (
                        1.0 - GetSoilLayer_GravelVol(layeri) / 100.0
                    )
                    drainsum, excess = CheckDrainsum(layeri, drainsum, excess)
                else:
                    drainsum = 0.0

            if theta_x > (GetSoilLayer_SAT(layeri) / 100.0):
                SetCompartment_theta(
                    compi,
                    GetCompartment_theta(compi)
                    + drainsum / (1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(layeri) / 100.0))
                )
                if GetCompartment_theta(compi) <= (GetSoilLayer_SAT(layeri) / 100.0):
                    if GetCompartment_theta(compi) > (GetCompartment_FCadj(compi) / 100.0):
                        if UseAdjustedFCCurve:
                            delta_theta = calculate_delta_theta_adjusted_fc(
                                GetCompartment_theta(compi),
                                (GetCompartment_FCadj(compi) / 100.0),
                                layeri
                            )
                        else:
                            delta_theta = calculate_delta_theta(
                                GetCompartment_theta(compi),
                                (GetCompartment_FCadj(compi) / 100.0),
                                layeri
                            )
                        SetCompartment_theta(compi, GetCompartment_theta(compi) - delta_theta)
                        drainsum = delta_theta * 1000.0 * GetCompartment_Thickness(compi) * (
                            1.0 - GetSoilLayer_GravelVol(layeri) / 100.0
                        )
                        drainsum, excess = CheckDrainsum(layeri, drainsum, excess)
                    else:
                        drainsum = 0.0

                if GetCompartment_theta(compi) > (GetSoilLayer_SAT(layeri) / 100.0):
                    excess = (GetCompartment_theta(compi) - (GetSoilLayer_SAT(layeri) / 100.0)) * 1000.0 * GetCompartment_Thickness(compi) * (
                        1.0 - GetSoilLayer_GravelVol(layeri) / 100.0
                    )
                    if UseAdjustedFCCurve:
                        delta_theta = calculate_delta_theta_adjusted_fc(GetCompartment_theta(compi), (GetCompartment_FCadj(compi) / 100.0), layeri)
                    else:
                        delta_theta = calculate_delta_theta(GetCompartment_theta(compi), (GetCompartment_FCadj(compi) / 100.0), layeri)
                    SetCompartment_theta(compi, (GetSoilLayer_SAT(layeri) / 100.0) - delta_theta)
                    drain_comp = delta_theta * 1000.0 * GetCompartment_Thickness(compi) * (
                        1.0 - GetSoilLayer_GravelVol(layeri) / 100.0
                    )

                    drainmax = delta_theta * 1000.0 * pre_thick * (
                        1.0 - GetSoilLayer_GravelVol(layeri) / 100.0
                    )
                    if drainmax > excess:
                        drainmax = excess
                    excess = excess - drainmax
                    drainsum = drainmax + drain_comp
                    drainsum, excess = CheckDrainsum(layeri, drainsum, excess)

        SetCompartment_fluxout(compi, drainsum)

        # 4. Redistribute excess
        # ======================
        if excess > 0.0:
            pre_nr = compi + 1
            while True:
                pre_nr = pre_nr - 1
                layeri = GetCompartment_Layer(pre_nr)
                if pre_nr < compi:
                    SetCompartment_fluxout(pre_nr, GetCompartment_fluxout(pre_nr) - excess)
                SetCompartment_theta(
                    pre_nr,
                    GetCompartment_theta(pre_nr)
                    + excess / (1000.0 * GetCompartment_Thickness(pre_nr) * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(pre_nr)) / 100.0))
                )
                if GetCompartment_theta(pre_nr) > (GetSoilLayer_SAT(layeri) / 100.0):
                    excess = (GetCompartment_theta(pre_nr) - GetSoilLayer_SAT(layeri) / 100.0) * 1000.0 * GetCompartment_Thickness(pre_nr) * (
                        1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(pre_nr)) / 100.0
                    )
                    SetCompartment_theta(pre_nr, GetSoilLayer_SAT(layeri) / 100.0)
                else:
                    excess = 0.0
                if (excess == 0.0) or (pre_nr == 1):
                    break
            # redistribute excess

    SetDrain(drainsum)

def CheckDrainsum(layeri, drainsum, excess):
    if drainsum > GetSoilLayer_InfRate(layeri):
        excess = excess + drainsum - GetSoilLayer_InfRate(layeri)
        drainsum = GetSoilLayer_InfRate(layeri)
    return drainsum, excess


def calculate_weighting_factors(Depth, Compartment):
    i = 0
    compi = 0
    CumDepth = 0.0
    xx = 0.0
    wx = 0.0

    while True:
        compi = compi + 1
        CumDepth = CumDepth + Compartment[compi - 1].Thickness
        if CumDepth > Depth:
            CumDepth = Depth
        wx = 1.016 * (1.0 - math.exp(-4.16 * CumDepth / Depth))
        Compartment[compi - 1].WFactor = wx - xx
        if Compartment[compi - 1].WFactor > 1.0:
            Compartment[compi - 1].WFactor = 1.0
        if Compartment[compi - 1].WFactor < 0.0:
            Compartment[compi - 1].WFactor = 0.0
        xx = wx
        if (CumDepth >= Depth) or (compi == GetNrCompartments()):
            break

    for i in range(compi + 1, GetNrCompartments() + 1):
        Compartment[i - 1].WFactor = 0.0

    return Compartment

def calculate_runoff(MaxDepth):
    SUM = 0.0
    CNA = 0.0
    Shower = 0.0
    term = 0.0
    S = 0.0
    CN2 = 0
    CN1 = 0
    CN3 = 0

    CN2 = roundc(GetSoil_CNvalue() * (100 + GetManagement_CNcorrection()) / 100.0, mold=1)
    if GetRainRecord_DataType() == datatype_Daily:
        if GetSimulParam_CNcorrection():
            SUM = calculate_relative_wetness_topsoil(SUM, MaxDepth)
            CN1, CN3 = DetermineCNIandIII(CN2, CN1, CN3)
            CNA = float(roundc(CN1 + (CN3 - CN1) * SUM, mold=1))
        else:
            CNA = float(CN2)
        Shower = GetRain()
    else:
        CNA = float(CN2)
        Shower = (GetRain() * 10.0) / GetSimulParam_EffectiveRain_ShowersInDecade()

    S = 254.0 * (100.0 / CNA - 1.0)
    term = Shower - (GetSimulParam_IniAbstract() / 100.0) * S
    if term <= 0.0:
        SetRunoff(0.0)
    else:
        SetRunoff((term ** 2) / (Shower + (1.0 - (GetSimulParam_IniAbstract() / 100.0)) * S))

    if (GetRunoff() > 0.0) and (
        (GetRainRecord_DataType() == datatype_Decadely)
        or (GetRainRecord_DataType() == datatype_Monthly)
    ):
        if GetRunoff() >= Shower:
            SetRunoff(GetRain())
        else:
            SetRunoff(GetRunoff() * (GetSimulParam_EffectiveRain_ShowersInDecade() / 10.14))
            if GetRunoff() > GetRain():
                SetRunoff(GetRain())
    

def calculate_relative_wetness_topsoil(SUM, MaxDepth):
    CumDepth = 0.0
    theta = 0.0
    compi = 0
    layeri = 0

    Compartment_temp = GetCompartment()
    calculate_weighting_factors(MaxDepth, Compartment_temp)
    SetCompartment(Compartment_temp)
    SUM = 0.0
    compi = 0
    CumDepth = 0.0

    while True:
        compi = compi + 1
        layeri = GetCompartment_Layer(compi)
        CumDepth = CumDepth + GetCompartment_Thickness(compi)
        if GetCompartment_theta(compi) < GetSoilLayer_WP(layeri) / 100.0:
            theta = GetSoilLayer_WP(layeri) / 100.0
        else:
            theta = GetCompartment_theta(compi)
        SUM = SUM + GetCompartment_WFactor(compi) \
            * (theta - GetSoilLayer_WP(layeri) / 100.0) \
            / (GetSoilLayer_FC(layeri) / 100.0 - GetSoilLayer_WP(layeri) / 100.0)
        if (CumDepth >= MaxDepth) or (compi == GetNrCompartments()):
            break

    if SUM < 0.0:
        SUM = 0.0
    if SUM > 1.0:
        SUM = 1.0

    return SUM


def Calculate_irrigation(SubDrain, TargetTimeVal, TargetDepthVal):
    ZrWC = 0.0
    RAWi = 0.0
    SWCtopSoilConsidered_temp = False

    # total root zone is considered
    SWCtopSoilConsidered_temp = GetSimulation_SWCtopSoilConsidered()
    SWCtopSoilConsidered_temp = DetermineRootZoneWC(GetRootingDepth(), SWCtopSoilConsidered_temp)
    SetSimulation_SWCtopSoilConsidered(SWCtopSoilConsidered_temp)
    ZrWC = GetRootZoneWC_Actual() - GetEpot() - GetTpot() + GetRain() - GetRunoff() - SubDrain

    if GetGenerateTimeMode() == GenerateTimeMode_AllDepl:
        if (GetRootZoneWC_FC() - ZrWC) >= TargetTimeVal:
            TargetTimeVal = 1
        else:
            TargetTimeVal = 0

    if GetGenerateTimeMode() == GenerateTimeMode_AllRAW:
        RAWi = (TargetTimeVal / 100.0) * (GetRootZoneWC_FC() - GetRootZoneWC_Thresh())
        if (GetRootZoneWC_FC() - ZrWC) >= RAWi:
            TargetTimeVal = 1
        else:
            TargetTimeVal = 0

    if TargetTimeVal == 1:
        if GetGenerateDepthMode() == GenerateDepthMode_FixDepth:
            SetIrrigation(float(TargetDepthVal))
        else:
            SetIrrigation((GetRootZoneWC_FC() - ZrWC) + TargetDepthVal)
            if GetIrrigation() < 0.0:
                SetIrrigation(0.0)
    else:
        SetIrrigation(0.0)

    return SubDrain, TargetTimeVal


def CalculateEffectiveRainfall(SubDrain):
    EffecRain = 0.0
    ETcropMonth = 0.0
    RainMonth = 0.0
    DrainMax = 0.0
    Zr = 0.0
    depthi = 0.0
    DTheta = 0.0
    RestTheta = 0.0
    compi = 0

    if GetRain() > 0.0:
        # 1. Effective Rainfall
        EffecRain = (GetRain() - GetRunoff())
        Method = GetSimulParam_EffectiveRain_Method()
        if Method == EffectiveRainMethod_Percentage:
            EffecRain = (GetSimulParam_EffectiveRain_PercentEffRain() / 100.0) * (GetRain() - GetRunoff())
        elif Method == EffectiveRainMethod_USDA:
            ETcropMonth = ((GetEpot() + GetTpot()) * 30.0) / 25.4  # inch/month
            RainMonth = ((GetRain() - GetRunoff()) * 30.0) / 25.4  # inch/Month
            if RainMonth > 0.1:
                EffecRain = (
                    (0.70917 * math.exp(0.82416 * math.log(RainMonth)) - 0.11556)
                    * (math.exp(0.02426 * ETcropMonth * math.log(10.0)))
                )  # inch/month
            else:
                EffecRain = RainMonth
            EffecRain = EffecRain * (25.4 / 30.0)  # mm/day

    if EffecRain < 0.0:
        EffecRain = 0.0
    if EffecRain > (GetRain() - GetRunoff()):
        EffecRain = (GetRain() - GetRunoff())
    SubDrain = (GetRain() - GetRunoff()) - EffecRain

    # 2. Verify Possibility of SubDrain
    if SubDrain > 0.0:
        DrainMax = GetSoilLayer_InfRate(1)
        if GetSurfaceStorage() > 0.0:
            DrainMax = 0.0
        else:
            Zr = GetRootingDepth()
            if Zr <= epsilon(0.0):
                Zr = (GetSimulParam_EvapZmax() / 100.0)
            compi = 0
            depthi = 0.0
            DTheta = (EffecRain / Zr) / 1000.0
            while True:
                compi = compi + 1
                depthi = depthi + GetCompartment_Thickness(compi)
                RestTheta = (
                    GetSoilLayer_SAT(GetCompartment_Layer(compi)) / 100.0
                    - (GetCompartment_theta(compi) + DTheta)
                )
                if RestTheta <= epsilon(0.0):
                    DrainMax = 0.0
                if GetSoilLayer_InfRate(GetCompartment_Layer(compi)) < DrainMax:
                    DrainMax = GetSoilLayer_InfRate(GetCompartment_Layer(compi))
                if (depthi >= Zr) or (compi >= GetNrCompartments()):
                    break

        if SubDrain > DrainMax:
            if GetManagement_BundHeight() < 0.001:
                SetRunoff(GetRunoff() + (SubDrain - DrainMax))
            SubDrain = DrainMax

    return SubDrain


def calculate_CapillaryRise(CRwater, CRsalt):
    Zbottom = 0.0
    MaxMM = 0.0
    DThetaMax = 0.0
    DTheta = 0.0
    LimitMM = 0.0
    CRcomp = 0.0
    SaltCRi = 0.0
    DrivingForce = 0.0
    ZtopNextLayer = 0.0
    Krel = 0.0
    ThetaThreshold = 0.0
    ThetaMinusWP = 0.0
    FCadjMinusWP = 0.0
    compi = 0
    SCellAct = 0
    layeri = 0
    DThetaIsNumericalLayerTop = False

    Zbottom = 0.0
    for compi in range(1, GetNrCompartments() + 1):
        Zbottom = Zbottom + GetCompartment_Thickness(compi)

    compi = GetNrCompartments()
    MaxMM = MaxCRatDepth(
        GetSoilLayer_CRa(GetCompartment_Layer(compi)),
        GetSoilLayer_CRb(GetCompartment_Layer(compi)),
        GetSoilLayer_InfRate(GetCompartment_Layer(compi)),
        (Zbottom - GetCompartment_Thickness(compi) / 2.0),
        (GetZiAqua() / 100.0)
    )

    ZtopNextLayer = 0.0
    for layeri in range(1, GetCompartment_Layer(GetNrCompartments()) + 1):
        ZtopNextLayer = ZtopNextLayer + GetSoilLayer_Thickness(layeri)

    layeri = GetCompartment_Layer(GetNrCompartments())
    while ((ZtopNextLayer < (GetZiAqua() / 100.0))
           and (layeri < GetSoil_NrSoilLayers())):
        layeri = layeri + 1
        LimitMM = MaxCRatDepth(
            GetSoilLayer_CRa(layeri),
            GetSoilLayer_CRb(layeri),
            GetSoilLayer_InfRate(layeri),
            ZtopNextLayer,
            (GetZiAqua() / 100.0)
        )
        if MaxMM > LimitMM:
            MaxMM = LimitMM
        ZtopNextLayer = ZtopNextLayer + GetSoilLayer_Thickness(layeri)

    while ((roundc(MaxMM * 1000.0, mold=1) > 0)
           and (compi > 0)
           and (roundc(GetCompartment_fluxout(compi) * 1000.0, mold=1) == 0)):
        DThetaMax = 0.0
        CRcomp = 0.0
        ThetaMinusWP = (
            GetCompartment_theta(compi)
            - GetSoilLayer_WP(GetCompartment_Layer(compi)) / 100.0
        )
        FCadjMinusWP = (
            GetCompartment_FCadj(compi) / 100.0
            - GetSoilLayer_WP(GetCompartment_Layer(compi)) / 100.0
        )

        if ((GetCompartment_theta(compi)
                >= GetSoilLayer_WP(GetCompartment_Layer(compi)) / 100.0)
                and (GetSimulParam_RootNrDF() > 0)):
            if ThetaMinusWP <= 0.0:
                DrivingForce = 1.0
            else:
                DrivingForce = 1.0 - (
                    math.exp(
                        GetSimulParam_RootNrDF() * math.log(ThetaMinusWP)
                    )
                    /
                    math.exp(
                        GetSimulParam_RootNrDF() * math.log(FCadjMinusWP)
                    )
                )
        else:
            DrivingForce = 1.0

        ThetaThreshold = (
            GetSoilLayer_WP(GetCompartment_Layer(compi)) / 100.0
            + GetSoilLayer_FC(GetCompartment_Layer(compi)) / 100.0
        ) / 2.0

        if GetCompartment_theta(compi) < ThetaThreshold:
            ThetaWP = GetSoilLayer_WP(GetCompartment_Layer(compi)) / 100.0
            if ((GetCompartment_theta(compi)
                 <= ThetaWP)
                or (ThetaThreshold <= ThetaWP)):
                Krel = 0.0
            else:
                Krel = (
                    GetCompartment_theta(compi)
                    - ThetaWP
                ) / (ThetaThreshold - ThetaWP)
        else:
            Krel = 1.0

        DTheta = GetCompartment_FCadj(compi) / 100.0 - GetCompartment_theta(compi)
        if (
            (Krel <= 0.0)
            and (DTheta > 0.0)
            and (abs(GetECiAqua()) <= sys.float_info.epsilon)
            and (abs(GetCompartment_theta(compi) - (GetSoilLayer_WP(GetCompartment_Layer(compi)) / 100.0)) <= sys.float_info.epsilon)
            and ((Zbottom - GetCompartment_Thickness(compi) / 2.0) < (GetZiAqua() / 100.0))
        ):
            Krel = 1e-16
        DThetaIsNumericalLayerTop = (
            (DTheta > 0.0)
            and (DTheta <= (sys.float_info.epsilon / 4.0))
            and (compi > 1)
            and (compi < GetNrCompartments())
            and (GetCompartment_Layer(compi + 1) == GetCompartment_Layer(compi))
            and (GetCompartment_Layer(compi - 1) != GetCompartment_Layer(compi))
        )
        if ((DTheta > 0.0)
            and (not DThetaIsNumericalLayerTop)
            and ((Zbottom - GetCompartment_Thickness(compi) / 2.0)
                 < (GetZiAqua() / 100.0))):
            DThetaMax = (
                Krel * DrivingForce * MaxMM
                / (1000.0 * GetCompartment_Thickness(compi))
            )

            if DTheta >= DThetaMax:
                SetCompartment_theta(
                    compi,
                    GetCompartment_theta(compi) + DThetaMax
                )
                CRcomp = (
                    DThetaMax * 1000.0 * GetCompartment_Thickness(compi)
                    * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
                )
                MaxMM = 0.0
            else:
                SetCompartment_theta(
                    compi,
                    GetCompartment_FCadj(compi) / 100.0
                )
                CRcomp = (
                    DTheta * 1000.0 * GetCompartment_Thickness(compi)
                    * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
                )
                MaxMM = Krel * MaxMM - CRcomp

            CRwater = CRwater + CRcomp

            SCellAct = ActiveCells(GetCompartment_i(compi))
            SaltCRi = Equiv * CRcomp * GetECiAqua()
            SetCompartment_Salt(
                compi,
                SCellAct,
                GetCompartment_Salt(compi, SCellAct) + SaltCRi
            )
            CRsalt = CRsalt + SaltCRi

        Zbottom = Zbottom - GetCompartment_Thickness(compi)
        compi = compi - 1
        if compi < 1:
            break

        LimitMM = MaxCRatDepth(
            GetSoilLayer_CRa(GetCompartment_Layer(compi)),
            GetSoilLayer_CRb(GetCompartment_Layer(compi)),
            GetSoilLayer_InfRate(GetCompartment_Layer(compi)),
            (Zbottom - GetCompartment_Thickness(compi) / 2.0),
            (GetZiAqua() / 100.0)
        )
        if MaxMM > LimitMM:
            MaxMM = LimitMM

    return CRwater, CRsalt



def CheckWaterSaltBalance(dayi,
              InfiltratedRain,
              control, InfiltratedIrrigation,
              InfiltratedStorage, Surf0, ECInfilt, ECdrain,
              HorizontalWaterFlow, HorizontalSaltFlow, SubDrain):
    compi = 0
    layeri = 0
    celli = 0
    Surf1 = 0.0
    ECw = 0.0

    if control == control_begin_day:
        SetTotalWaterContent_BeginDay(0.0)  # mm
        Surf0 = GetSurfaceStorage()  # mm
        SetTotalSaltContent_BeginDay(0.0)  # Mg/ha
        for compi in range(1, GetNrCompartments() + 1):
            SetTotalWaterContent_BeginDay(GetTotalWaterContent_BeginDay()
               + GetCompartment_theta(compi) * 1000.0 *
                 GetCompartment_Thickness(compi)
               * (1.0 -
                  GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0))
            SetCompartment_fluxout(compi, 0.0)
            for celli in range(1, GetSoilLayer_SCP1(GetCompartment_Layer(compi)) + 1):
                SetTotalSaltContent_BeginDay(
                       GetTotalSaltContent_BeginDay()
                       + (GetCompartment_Salt(compi, celli) +
                          GetCompartment_Depo(compi, celli)) / 100.0)  # Mg/ha
        SetDrain(0.0)
        SetRunoff(0.0)
        # Eact is set to 0 at the beginning of the evaporation process
        SetTact(0.0)
        SetInfiltrated(0.0)
        ECInfilt = 0.0
        SubDrain = 0.0
        ECdrain = 0.0
        HorizontalWaterFlow = 0.0
        HorizontalSaltFlow = 0.0
        SetCRwater(0.0)
        SetCRsalt(0.0)

    elif control == control_end_day:

        SetInfiltrated(InfiltratedRain + InfiltratedIrrigation
                            + InfiltratedStorage)
        for layeri in range(1, GetSoil_NrSoilLayers() + 1):
            SetSoilLayer_WaterContent(layeri, 0.0)
        SetTotalWaterContent_EndDay(0.0)
        Surf1 = GetSurfaceStorage()
        SetTotalSaltContent_EndDay(0.0)

        # quality of irrigation water
        if dayi < GetCrop_Day1():
            ECw = GetIrriECw_PreSeason()
        else:
            ECw = GetSimulation_IrriECw()
            if dayi > GetCrop_DayN():
                ECw = GetIrriECw_PostSeason()

        for compi in range(1, GetNrCompartments() + 1):
            SetTotalWaterContent_EndDay(GetTotalWaterContent_EndDay()
               + GetCompartment_theta(compi) * 1000.0 *
                 GetCompartment_Thickness(compi)
               * (1.0 -
                  GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0))
            SetSoilLayer_WaterContent(GetCompartment_Layer(compi),
                    GetSoilLayer_WaterContent(GetCompartment_Layer(compi))
                    + GetCompartment_theta(compi) * 1000.0 *
                          GetCompartment_Thickness(compi)
                    * (1.0 -
                       GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0))
            for celli in range(1, GetSoilLayer_SCP1(GetCompartment_Layer(compi)) + 1):
                SetTotalSaltContent_EndDay(GetTotalSaltContent_EndDay()
                   + (GetCompartment_Salt(compi, celli) +
                      GetCompartment_Depo(compi, celli)) / 100.0)  # Mg/ha
        SetTotalWaterContent_ErrorDay(GetTotalWaterContent_BeginDay()
                + Surf0
                - (GetTotalWaterContent_EndDay() + GetDrain() + GetRunoff() + GetEact()
                + GetTact() + Surf1 - GetRain() - GetIrrigation() - GetCRwater() - HorizontalWaterFlow))
        SetTotalSaltContent_ErrorDay(GetTotalSaltContent_BeginDay()
                - GetTotalSaltContent_EndDay()  # Mg/ha
                + InfiltratedIrrigation * ECw * Equiv / 100.0
                + InfiltratedStorage * ECInfilt * Equiv / 100.0
                - GetDrain() * ECdrain * Equiv / 100.0
                + GetCRsalt() / 100.0
                + HorizontalSaltFlow)


        SetSumWaBal_Epot(GetSumWaBal_Epot() + GetEpot())
        SetSumWaBal_Tpot(GetSumWaBal_Tpot() + GetTpot())
        SetSumWaBal_Rain(GetSumWaBal_Rain() + GetRain())
        SetSumWaBal_Irrigation(GetSumWaBal_Irrigation() + GetIrrigation())
        SetSumWaBal_Infiltrated(GetSumWaBal_Infiltrated() +
                  GetInfiltrated())
        SetSumWaBal_Runoff(GetSumWaBal_Runoff() + GetRunoff())
        SetSumWaBal_Drain(GetSumWaBal_Drain() + GetDrain())
        SetSumWaBal_Eact(GetSumWaBal_Eact() + GetEact())
        SetSumWaBal_Tact(GetSumWaBal_Tact() + GetTact())
        SetSumWaBal_TrW(GetSumWaBal_TrW() + GetTactWeedInfested())
        SetSumWaBal_CRwater(GetSumWaBal_CRwater() + GetCRwater())


        if (((dayi - GetSimulation_DelayedDays()) >= GetCrop_Day1())
            and ((dayi - GetSimulation_DelayedDays()) <= GetCrop_DayN())):
            # in growing cycle
            if GetSumWaBal_Biomass() > 0.0:
                # biomass was already produced (i.e. CC present)
                # and still canopy cover
                if GetCCiActual() > 0.0:
                    SetSumWaBal_ECropCycle(GetSumWaBal_ECropCycle()
                           + GetEact())
            else:
                SetSumWaBal_ECropCycle(GetSumWaBal_ECropCycle()
                           + GetEact())  # before germination
        SetSumWaBal_CRSalt(GetSumWaBal_CRSalt() + GetCRsalt() / 100.0)
        SetSumWaBal_SaltIn(GetSumWaBal_SaltIn() +
               (InfiltratedIrrigation * ECw + InfiltratedStorage * ECInfilt) * Equiv / 100.0)
        SetSumWaBal_SaltOut(GetSumWaBal_SaltOut() +
                GetDrain() * ECdrain * Equiv / 100.0)

    return Surf0, ECInfilt, ECdrain, HorizontalWaterFlow, HorizontalSaltFlow, SubDrain

def calculate_saltcontent(InfiltratedRain, InfiltratedIrrigation,
                          InfiltratedStorage, SubDrain, dayi):
    SaltIN = 0.0
    SaltOUT = 0.0
    mmIN = 0.0
    DeltaTheta = 0.0
    Theta = 0.0
    SAT = 0.0
    mm1 = 0.0
    mm2 = 0.0
    Dx = 0.0
    limit = 0.0
    Dif = 0.0
    UL = 0.0

    Zr = 0.0
    depthi = 0.0
    ECsubdrain = 0.0
    ECcel = 0.0
    DeltaZ = 0.0
    ECsw1 = 0.0
    ECsw2 = 0.0
    ECsw = 0.0
    SM1 = 0.0
    SM2 = 0.0
    DS1 = 0.0
    DS2 = 0.0
    DS = 0.0

    compi = 0
    celi = 0
    celiM1 = 0
    Ni = 0
    ECw = 0.0
    Salt_temp = 0.0
    Salt2_temp = 0.0
    Depo_temp = 0.0
    Depo2_temp = 0.0
    Compi_temp = None

    mmIN = InfiltratedRain + InfiltratedIrrigation + InfiltratedStorage

    # quality of irrigation water
    if dayi < GetCrop_Day1():
        ECw = GetIrriECw_PreSeason()
    else:
        ECw = GetSimulation_IrriECw()
        if dayi > GetCrop_DayN():
            ECw = GetIrriECw_PostSeason()

    # initialise salt balance
    SaltIN = InfiltratedIrrigation * ECw * Equiv + InfiltratedStorage * GetECstorage() * Equiv
    SetSaltInfiltr(SaltIN / 100.0)
    # salt infiltrated in soil profile kg/ha
    SaltOUT = 0.0

    for compi in range(1, int(GetNrCompartments()) + 1):
        # 0. Set compartment parameters
        SAT = (GetSoilLayer_SAT(GetCompartment_Layer(compi))) / 100.0  # m3/m3
        UL = GetSoilLayer_UL(GetCompartment_Layer(compi))  # m3/m3
        # Upper limit of SC salt cel
        Dx = GetSoilLayer_Dx(GetCompartment_Layer(compi))  # m3/m3
        # Size of salts cel (expect last one)

        # 1. Initial situation before drain and infiltration
        DeltaTheta = mmIN / (1000.0 * GetCompartment_Thickness(compi)
                             * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0))
        Theta = GetCompartment_theta(compi) - DeltaTheta + GetCompartment_fluxout(compi) / (1000.0 * GetCompartment_Thickness(compi))

        # 2. Determine active SaltCels and Add IN
        Theta = Theta + DeltaTheta
        if Theta <= UL:
            celi = 0
            while Theta > Dx * celi:
                celi = celi + 1
        else:
            celi = GetSoilLayer_SCP1(GetCompartment_Layer(compi))
        if celi == 0:
            celi = 1  # XXX would be best to avoid celi=0 to begin with
        if DeltaTheta > 0.0:
            SetCompartment_Salt(compi, celi, GetCompartment_Salt(compi, celi) + SaltIN)

        # 3. Mixing
        if celi > 1:
            for Ni in range(1, int(celi - 1) + 1):
                mm1 = Dx * 1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
                if Ni < GetSoilLayer_SC(GetCompartment_Layer(compi)):
                    mm2 = mm1
                elif Theta > SAT:
                    mm2 = (Theta - UL) * 1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
                else:
                    mm2 = (SAT - UL) * 1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)

                Dif = GetSoilLayer_SaltMobility_i(GetCompartment_Layer(compi), Ni)
                Salt_temp = GetCompartment_Salt(compi, Ni)
                Salt2_temp = GetCompartment_Salt(compi, Ni + 1)
                Depo_temp = GetCompartment_Depo(compi, Ni)
                Depo2_temp = GetCompartment_Depo(compi, Ni + 1)

                Salt_temp, Salt2_temp, Depo_temp, Depo2_temp = Mixing(Dif, mm1, mm2, Salt_temp, Salt2_temp, Depo_temp, Depo2_temp)

                SetCompartment_Salt(compi, Ni, Salt_temp)
                SetCompartment_Salt(compi, Ni + 1, Salt2_temp)
                SetCompartment_Depo(compi, Ni, Depo_temp)
                SetCompartment_Depo(compi, Ni + 1, Depo2_temp)

        # 4. Drain
        SaltOUT = 0.0
        if GetCompartment_fluxout(compi) > 0.0:
            DeltaTheta = GetCompartment_fluxout(compi) / (1000.0 * GetCompartment_Thickness(compi)
                                                         * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0))
            while DeltaTheta > 0.0:
                if celi < GetSoilLayer_SCP1(GetCompartment_Layer(compi)):
                    limit = (celi - 1.0) * Dx
                else:
                    limit = UL

                if (Theta - DeltaTheta) < limit:
                    SaltOUT = SaltOUT + GetCompartment_Salt(compi, celi) + GetCompartment_Depo(compi, celi)
                    SetCompartment_Salt(compi, celi, 0.0)
                    mm1 = (Theta - limit) * 1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
                    if SaltOUT > (GetSimulParam_SaltSolub() * mm1):
                        SetCompartment_Depo(compi, celi, SaltOUT - (GetSimulParam_SaltSolub() * mm1))
                        SaltOUT = (GetSimulParam_SaltSolub() * mm1)
                    else:
                        SetCompartment_Depo(compi, celi, 0.0)
                    DeltaTheta = DeltaTheta - (Theta - limit)
                    Theta = limit
                    celi = celi - 1
                    if celi < 1:
                        celi = 1
                        DeltaTheta = 0.0
                else:
                    SaltOUT = SaltOUT + (GetCompartment_Salt(compi, celi) + GetCompartment_Depo(compi, celi)) * (DeltaTheta / (Theta - limit))
                    SetCompartment_Salt(compi, celi, GetCompartment_Salt(compi, celi) * (1.0 - DeltaTheta / (Theta - limit)))
                    SetCompartment_Depo(compi, celi, GetCompartment_Depo(compi, celi) * (1.0 - DeltaTheta / (Theta - limit)))
                    mm1 = DeltaTheta * 1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
                    if SaltOUT > (GetSimulParam_SaltSolub() * mm1):
                        SetCompartment_Depo(compi, celi,
                                            GetCompartment_Depo(compi, celi)
                                            + (SaltOUT - GetSimulParam_SaltSolub() * mm1))
                        SaltOUT = (GetSimulParam_SaltSolub() * mm1)
                    DeltaTheta = 0.0
                    mm1 = GetSoilLayer_Dx(GetCompartment_Layer(compi)) * 1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
                    if celi == GetSoilLayer_SCP1(GetCompartment_Layer(compi)):
                        mm1 = 2.0 * mm1
                    Salt_temp = GetCompartment_Salt(compi, celi)
                    Depo_temp = GetCompartment_Depo(compi, celi)
                    Salt_temp, Depo_temp = SaltSolutionDeposit(mm1, Salt_temp, Depo_temp)
                    SetCompartment_Salt(compi, celi, Salt_temp)
                    SetCompartment_Depo(compi, celi, Depo_temp)

        mmIN = GetCompartment_fluxout(compi)
        SaltIN = SaltOUT

    if GetDrain() > 0.001:
        SetECDrain(SaltOUT / (GetDrain() * Equiv))

    # 5. vertical salt diffusion
    celi = ActiveCells(GetCompartment_i(1))
    SM2 = GetSoilLayer_SaltMobility_i(GetCompartment_Layer(1), celi) / 4.0
    ECsw2 = ECswComp(GetCompartment_i(1), False)  # not at FC
    mm2 = GetCompartment_theta(1) * 1000.0 * GetCompartment_Thickness(1) * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(1)) / 100.0)
    for compi in range(2, int(GetNrCompartments()) + 1):
        celiM1 = celi
        SM1 = SM2
        ECsw1 = ECsw2
        mm1 = mm2
        celi = ActiveCells(GetCompartment_i(compi))
        SM2 = GetSoilLayer_SaltMobility_i(GetCompartment_Layer(compi), celi) / 4.0
        ECsw2 = ECswComp(GetCompartment_i(compi), False)  # not at FC
        mm2 = GetCompartment_theta(compi) * 1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
        ECsw = (ECsw1 * mm1 + ECsw2 * mm2) / (mm1 + mm2)
        DS1 = (ECsw1 - (ECsw1 + (ECsw - ECsw1) * SM1)) * mm1 * Equiv
        DS2 = (ECsw2 - (ECsw2 + (ECsw - ECsw2) * SM2)) * mm2 * Equiv
        if abs(DS2) < abs(DS1):
            DS = abs(DS2)
        else:
            DS = abs(DS1)
        if DS > 0.0:
            if ECsw1 > ECsw:
                DS = DS * (-1.0)
            Compi_temp = GetCompartment_i(compi - 1)
            MoveSaltTo(Compi_temp, celiM1, DS)
            SetCompartment_i(compi - 1, Compi_temp)
            DS = DS * (-1.0)
            Compi_temp = GetCompartment_i(compi)
            MoveSaltTo(Compi_temp, celi, DS)
            SetCompartment_i(compi, Compi_temp)

    # 6. Internal salt movement as a result of SubDrain
    # SubDrain part of non-effective rainfall (10-day & monthly input)
    if SubDrain > 0.0:
        Zr = GetRootingDepth()
        if Zr >= epsilon(0.0):
            Zr = (GetSimulParam_EvapZmax() / 100.0)  # in meter
        compi = 0
        depthi = 0.0
        ECsubdrain = 0.0

        # extract
        while True:
            compi = compi + 1
            depthi = depthi + GetCompartment_Thickness(compi)
            if depthi <= Zr:
                DeltaZ = GetCompartment_Thickness(compi)
            else:
                DeltaZ = GetCompartment_Thickness(compi) - (depthi - Zr)

            celi = ActiveCells(GetCompartment_i(compi))
            if celi < GetSoilLayer_SCP1(GetCompartment_Layer(compi)):
                mm1 = GetSoilLayer_Dx(GetCompartment_Layer(compi)) * 1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
            else:
                mm1 = 2.0 * GetSoilLayer_Dx(GetCompartment_Layer(compi)) * 1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)

            ECcel = GetCompartment_Salt(compi, celi) / (mm1 * Equiv)
            ECsubdrain = (ECcel * mm1 * (DeltaZ / GetCompartment_Thickness(compi)) + ECsubdrain * SubDrain) / (mm1 * (DeltaZ / GetCompartment_Thickness(compi)) + SubDrain)

            SetCompartment_Salt(
                compi, celi,
                (1.0 - (DeltaZ / GetCompartment_Thickness(compi))) * GetCompartment_Salt(compi, celi)
                + (DeltaZ / GetCompartment_Thickness(compi)) * ECsubdrain * mm1 * Equiv
            )

            Salt_temp = GetCompartment_Salt(compi, celi)
            Depo_temp = GetCompartment_Depo(compi, celi)
            Salt_temp, Depo_temp = SaltSolutionDeposit(mm1, Salt_temp, Depo_temp)
            SetCompartment_Salt(compi, celi, Salt_temp)
            SetCompartment_Depo(compi, celi, Depo_temp)

            if (depthi >= Zr) or (compi >= GetNrCompartments()):
                break

        # dump
        if compi >= GetNrCompartments():
            SaltOUT = GetECDrain() * (GetDrain() * Equiv) + ECsubdrain * SubDrain * Equiv
            SetECDrain(SaltOUT / (GetDrain() * Equiv))
        else:
            compi = compi + 1
            celi = ActiveCells(GetCompartment_i(compi))
            if celi < GetSoilLayer_SCP1(GetCompartment_Layer(compi)):
                mm1 = GetSoilLayer_Dx(GetCompartment_Layer(compi)) * 1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
            else:
                mm1 = 2.0 * GetSoilLayer_Dx(GetCompartment_Layer(compi)) * 1000.0 * GetCompartment_Thickness(compi) * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)

            SetCompartment_Salt(compi, celi, GetCompartment_Salt(compi, celi) + ECsubdrain * SubDrain * Equiv)

            Salt_temp = GetCompartment_Salt(compi, celi)
            Depo_temp = GetCompartment_Depo(compi, celi)
            Salt_temp, Depo_temp = SaltSolutionDeposit(mm1, Salt_temp, Depo_temp)
            SetCompartment_Salt(compi, celi, Salt_temp)
            SetCompartment_Depo(compi, celi, Depo_temp)

def Mixing(Dif, mm1, mm2, Salt1, Salt2, Depo1, Depo2):
    EC1 = 0.0
    EC2 = 0.0
    ECmix = 0.0

    Salt1, Depo1 = SaltSolutionDeposit(mm1, Salt1, Depo1)
    EC1 = Salt1 / (mm1 * Equiv)
    Salt2, Depo2 = SaltSolutionDeposit(mm2, Salt2, Depo2)
    EC2 = Salt2 / (mm2 * Equiv)
    ECmix = (EC1 * mm1 + EC2 * mm2) / (mm1 + mm2)
    EC1 = EC1 + (ECmix - EC1) * Dif
    EC2 = EC2 + (ECmix - EC2) * Dif
    Salt1 = EC1 * mm1 * Equiv
    Salt1, Depo1 = SaltSolutionDeposit(mm1, Salt1, Depo1)
    Salt2 = EC2 * mm2 * Equiv
    Salt2, Depo2 = SaltSolutionDeposit(mm2, Salt2, Depo2)

    return Salt1, Salt2, Depo1, Depo2


def MoveSaltTo(Compx, celx, DS):
    mmx = 0.0
    celx_local = 0

    celx_local = celx
    if DS >= epsilon(0.0):
        i0 = celx_local - 1
        Compx.Salt[i0] = Compx.Salt[i0] + DS
        mmx = GetSoilLayer_Dx(Compx.Layer) * 1000.0 * Compx.Thickness \
                * (1.0 - GetSoilLayer_GravelVol(Compx.Layer) / 100.0)
        if celx_local == GetSoilLayer_SCP1(Compx.Layer):
            mmx = 2.0 * mmx
        Compx.Salt[i0], Compx.Depo[i0] = SaltSolutionDeposit(mmx, Compx.Salt[i0], Compx.Depo[i0])
    else:
        celx_local = GetSoilLayer_SCP1(Compx.Layer)
        i0 = celx_local - 1
        Compx.Salt[i0] = Compx.Salt[i0] + DS
        mmx = 2.0 * GetSoilLayer_Dx(Compx.Layer) * 1000.0 * Compx.Thickness \
                * (1.0 - GetSoilLayer_GravelVol(Compx.Layer) / 100.0)
        Compx.Salt[i0], Compx.Depo[i0] = SaltSolutionDeposit(mmx, Compx.Salt[i0], Compx.Depo[i0])
        mmx = mmx / 2.0
        while Compx.Salt[celx_local - 1] < 0.0:
            # index zero problem
            # TO DO: likely also happened with original Pascal code, 
            #        but Pascal code tolerates it
            if celx_local == 1:
                celx_local = len(Compx.Salt)
            Compx.Salt[celx_local - 2] = Compx.Salt[celx_local - 2] + Compx.Salt[celx_local - 1]
            Compx.Salt[celx_local - 1] = 0.0
            celx_local = celx_local - 1
            i0 = celx_local - 1
            Compx.Salt[i0], Compx.Depo[i0] = SaltSolutionDeposit(mmx, Compx.Salt[i0], Compx.Depo[i0])


def calculate_Extra_runoff(InfiltratedRain, InfiltratedIrrigation,
                           InfiltratedStorage, SubDrain):

    InfiltratedStorage = 0.0
    InfiltratedRain = GetRain() - GetRunoff()
    if InfiltratedRain > 0.0:
        FracSubDrain = SubDrain / InfiltratedRain
    else:
        FracSubDrain = 0.0

    if (GetIrrigation() + InfiltratedRain) > GetSoilLayer_InfRate(GetCompartment_Layer(1)):
        if GetIrrigation() > GetSoilLayer_InfRate(GetCompartment_Layer(1)):
            InfiltratedIrrigation = GetSoilLayer_InfRate(GetCompartment_Layer(1))
            SetRunoff(GetRain() + (GetIrrigation() - InfiltratedIrrigation))
            InfiltratedRain = 0.0
            SubDrain = 0.0
        else:
            InfiltratedIrrigation = GetIrrigation()
            InfiltratedRain = GetSoilLayer_InfRate(GetCompartment_Layer(1)) - InfiltratedIrrigation
            SubDrain = FracSubDrain * InfiltratedRain
            SetRunoff(GetRain() - InfiltratedRain)
    else:
        InfiltratedIrrigation = GetIrrigation()

    return InfiltratedRain, InfiltratedIrrigation, InfiltratedStorage, SubDrain

def calculate_surfacestorage(InfiltratedRain, InfiltratedIrrigation,
                             InfiltratedStorage, ECinfilt, SubDrain,
                             dayi):

    InfiltratedRain = 0.0
    InfiltratedIrrigation = 0.0
    if GetRainRecord_DataType() == datatype_Daily:
        Sum = GetSurfaceStorage() + GetIrrigation() + GetRain()
    else:
        Sum = GetSurfaceStorage() + GetIrrigation() + GetRain() - GetRunoff() - SubDrain

    if Sum > 0.0:
        # quality of irrigation water
        if dayi < GetCrop_Day1():
            ECw = GetIrriECw_PreSeason()
        else:
            ECw = GetSimulation_IrriECw()
            if dayi > GetCrop_DayN():
                ECw = GetIrriECw_PostSeason()

        # quality of stored surface water
        SetECstorage((GetECstorage() * GetSurfaceStorage() + ECw * GetIrrigation()) / Sum)

        # quality of infiltrated water (rain and/or irrigation and/or stored surface water)
        ECinfilt = GetECstorage()

        # surface storage
        if Sum > GetSoilLayer_InfRate(GetCompartment_Layer(1)):
            InfiltratedStorage = GetSoilLayer_InfRate(GetCompartment_Layer(1))
            SetSurfaceStorage(Sum - InfiltratedStorage)
        else:
            if GetRainRecord_DataType() == datatype_Daily:
                InfiltratedStorage = Sum
            else:
                InfiltratedStorage = GetSurfaceStorage() + GetIrrigation()
                InfiltratedRain = GetRain() - GetRunoff()
            SetSurfaceStorage(0.0)

        # extra run-off
        if GetSurfaceStorage() > (GetManagement_BundHeight() * 1000.0):
            SetRunoff(GetRunoff() + (GetSurfaceStorage() - GetManagement_BundHeight() * 1000.0))
            SetSurfaceStorage(GetManagement_BundHeight() * 1000.0)
    else:
        InfiltratedStorage = 0.0
        SetECstorage(0.0)

    return InfiltratedRain, InfiltratedIrrigation, InfiltratedStorage, ECinfilt

def calculate_infiltration(InfiltratedRain, InfiltratedIrrigation,
                           InfiltratedStorage, SubDrain):
    compi = 0
    layeri = 0
    pre_comp = 0
    RunoffIni = 0.0
    amount_still_to_store = 0.0
    factor = 0.0
    delta_theta_nul = 0.0
    delta_theta_SAT = 0.0
    theta_nul = 0.0
    drain_max = 0.0
    diff = 0.0
    excess = 0.0
    EffecRain = 0.0
    Zr = 0.0
    depthi = 0.0
    DeltaZ = 0.0
    StorableMM = 0.0

    # A -  INFILTRATION versus STORAGE in Rootzone (= EffecRain)
    if GetRainRecord_DataType() == datatype_Daily:
        amount_still_to_store = InfiltratedRain + InfiltratedIrrigation + InfiltratedStorage
        EffecRain = 0.0
    else:
        amount_still_to_store = InfiltratedIrrigation + InfiltratedStorage
        EffecRain = InfiltratedRain - SubDrain

    # B - INFILTRATION through TOP soil surface
    if amount_still_to_store > 0.0:
        RunoffIni = GetRunoff()
        compi = 0

        while True:
            compi = compi + 1
            layeri = GetCompartment_Layer(compi)

            #1. Calculate multiplication factor
            #====================================
            factor = Calculate_factor(layeri, compi)

            #2. Calculate theta nul
            #========================
            delta_theta_nul = amount_still_to_store / (1000.0 * GetCompartment_Thickness(compi)
                                                       * (1.0 - GetSoilLayer_GravelVol(layeri) / 100.0))
            delta_theta_SAT = calculate_delta_theta(GetSoilLayer_SAT(layeri) / 100.0,
                                                    GetSoilLayer_FC(layeri) / 100.0,
                                                    layeri)

            if delta_theta_nul < delta_theta_SAT:
                theta_nul = calculate_theta(delta_theta_nul,
                                            GetSoilLayer_FC(layeri) / 100.0,
                                            layeri)
                if theta_nul <= (GetCompartment_FCadj(compi) / 100.0):
                    theta_nul = GetCompartment_FCadj(compi) / 100.0
                    delta_theta_nul = calculate_delta_theta(theta_nul,
                                                            GetSoilLayer_FC(layeri) / 100.0,
                                                            layeri)
                if theta_nul > GetSoilLayer_SAT(layeri) / 100.0:
                    theta_nul = GetSoilLayer_SAT(layeri) / 100.0
            else:
                theta_nul = GetSoilLayer_SAT(layeri) / 100.0
                delta_theta_nul = delta_theta_SAT

            #3. Calculate drain max
            #========================
            drain_max = factor * delta_theta_nul * 1000.0 * GetCompartment_Thickness(compi) \
                        * (1.0 - GetSoilLayer_GravelVol(layeri) / 100.0)
            if (GetCompartment_fluxout(compi) + drain_max) > GetSoilLayer_InfRate(layeri):
                drain_max = GetSoilLayer_InfRate(layeri) - GetCompartment_fluxout(compi)

            #4. Store water
            #================
            diff = theta_nul - GetCompartment_theta(compi)
            if diff > 0.0:
                SetCompartment_theta(compi, GetCompartment_theta(compi)
                                     + amount_still_to_store
                                     / (1000.0 * GetCompartment_Thickness(compi)
                                        * (1.0 - GetSoilLayer_GravelVol(layeri) / 100.0)))
                if GetCompartment_theta(compi) > theta_nul:
                    amount_still_to_store = (GetCompartment_theta(compi) - theta_nul) \
                                            * 1000.0 * GetCompartment_Thickness(compi) \
                                            * (1.0 - GetSoilLayer_GravelVol(layeri) / 100.0)
                    SetCompartment_theta(compi, theta_nul)
                else:
                    amount_still_to_store = 0.0
            SetCompartment_fluxout(compi, GetCompartment_fluxout(compi) + amount_still_to_store)

            #5. Redistribute excess
            #========================
            excess = amount_still_to_store - drain_max
            if excess < 0.0:
                excess = 0.0
            amount_still_to_store = amount_still_to_store - excess

            if excess > 0.0:
                pre_comp = compi + 1
                while True:
                    pre_comp = pre_comp - 1
                    layeri = GetCompartment_Layer(pre_comp)
                    SetCompartment_fluxout(pre_comp, GetCompartment_fluxout(pre_comp) - excess)
                    SetCompartment_theta(pre_comp, GetCompartment_theta(pre_comp)
                                         + excess / (1000.0
                                                     * GetCompartment_Thickness(pre_comp)
                                                     * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(pre_comp)) / 100.0)))
                    if GetCompartment_theta(pre_comp) > GetSoilLayer_SAT(layeri) / 100.0:
                        excess = (GetCompartment_theta(pre_comp) - GetSoilLayer_SAT(layeri) / 100.0) \
                                 * 1000.0 * GetCompartment_Thickness(pre_comp) \
                                 * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(pre_comp)) / 100.0)
                        SetCompartment_theta(pre_comp, GetSoilLayer_SAT(layeri) / 100.0)
                    else:
                        excess = 0.0
                    if (excess == 0.0) or (pre_comp == 1):
                        break
                if excess > 0.0:
                    SetRunoff(GetRunoff() + excess)

            if (amount_still_to_store <= 0.0) or (compi == GetNrCompartments()):
                break

        if amount_still_to_store > 0.0:
            SetDrain(GetDrain() + amount_still_to_store)

        #6. Adjust infiltrated water
        #=============================
        if GetRunoff() > RunoffIni:
            if GetManagement_BundHeight() >= 0.01:
                SetSurfaceStorage(GetSurfaceStorage() + (GetRunoff() - RunoffIni))
                InfiltratedStorage = InfiltratedStorage - (GetRunoff() - RunoffIni)
                if GetSurfaceStorage() > GetManagement_BundHeight() * 1000.0:
                    SetRunoff(RunoffIni + (GetSurfaceStorage() - GetManagement_BundHeight() * 1000.0))
                    SetSurfaceStorage(GetManagement_BundHeight() * 1000.0)
                else:
                    SetRunoff(RunoffIni)
            else:
                InfiltratedRain = InfiltratedRain - (GetRunoff() - RunoffIni)
                if InfiltratedRain < 0.0:
                    InfiltratedIrrigation = InfiltratedIrrigation + InfiltratedRain
                    InfiltratedRain = 0.0

            # INFILTRATION through TOP soil surface

    # C - STORAGE in Subsoil (= SubDrain)
    if SubDrain > 0.0:
        amount_still_to_store = SubDrain

        # Where to store
        Zr = GetRootingDepth()
        if Zr <= 0.0:
            Zr = GetSimulParam_EvapZmax() / 100.0
        compi = 0
        depthi = 0.0
        while True:
            compi = compi + 1
            depthi = depthi + GetCompartment_Thickness(compi)
            if (depthi >= Zr) or (compi >= GetNrCompartments()):
                break
        if depthi > Zr:
            DeltaZ = (depthi - Zr)
        else:
            DeltaZ = 0.0

        # Store
        while (amount_still_to_store > 0.0) and ((compi < GetNrCompartments()) or (DeltaZ > 0.0)):
            if DeltaZ == 0.0:
                compi = compi + 1
                DeltaZ = GetCompartment_Thickness(compi)
            StorableMM = (GetSoilLayer_SAT(GetCompartment_Layer(compi)) / 100.0
                          - GetCompartment_theta(compi)) * 1000.0 * DeltaZ * (1.0
                          - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
            if StorableMM > amount_still_to_store:
                SetCompartment_theta(compi, GetCompartment_theta(compi)
                                     + amount_still_to_store
                                     / (1000.0 * GetCompartment_Thickness(compi)
                                        * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)))
                amount_still_to_store = 0.0
            else:
                amount_still_to_store = amount_still_to_store - StorableMM
                SetCompartment_theta(compi, GetCompartment_theta(compi)
                                     + StorableMM / (1000.0
                                                     * GetCompartment_Thickness(compi)
                                                     * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)))
            DeltaZ = 0.0
            if amount_still_to_store > GetSoilLayer_InfRate(GetCompartment_Layer(compi)):
                SubDrain = SubDrain - (amount_still_to_store - GetSoilLayer_InfRate(GetCompartment_Layer(compi)))
                EffecRain = EffecRain + (amount_still_to_store - GetSoilLayer_InfRate(GetCompartment_Layer(compi)))
                amount_still_to_store = GetSoilLayer_InfRate(GetCompartment_Layer(compi))

        # excess
        if amount_still_to_store > 0.0:
            SetDrain(GetDrain() + amount_still_to_store)
        # STORAGE in Subsoil (= SubDrain)
    # end if

    # D - STORAGE in Rootzone (= EffecRain)
    if EffecRain > 0.0:
        Zr = GetRootingDepth()
        if Zr <= 0.0:
            Zr = GetSimulParam_EvapZmax() / 100.0
        amount_still_to_store = EffecRain

        # Store
        # step 1 fill to FC (from top to bottom)
        compi = 0
        depthi = 0.0
        while True:
            compi = compi + 1
            depthi = depthi + GetCompartment_Thickness(compi)
            if depthi <= Zr:
                DeltaZ = GetCompartment_Thickness(compi)
            else:
                DeltaZ = GetCompartment_Thickness(compi) - (depthi - Zr)
            StorableMM = (GetCompartment_FCadj(compi) / 100.0
                          - GetCompartment_theta(compi)) * 1000.0 * DeltaZ \
                         * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
            if StorableMM < 0.0:
                StorableMM = 0.0
            if StorableMM > amount_still_to_store:
                SetCompartment_theta(compi, GetCompartment_theta(compi)
                                     + amount_still_to_store
                                     / (1000.0 * GetCompartment_Thickness(compi)
                                        * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)))
                amount_still_to_store = 0.0
            elif StorableMM > 0.0:
                SetCompartment_theta(compi, GetCompartment_theta(compi)
                                     + StorableMM
                                     / (1000.0 * GetCompartment_Thickness(compi)
                                        * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)))
                amount_still_to_store = amount_still_to_store - StorableMM
            if (depthi >= Zr) or (compi >= GetNrCompartments()) or (amount_still_to_store <= 0.0):
                break

        # step 2 fill to SATURATION (from bottom to top)
        if amount_still_to_store > 0.0:
            while True:
                if depthi > Zr:
                    DeltaZ = GetCompartment_Thickness(compi) - (depthi - Zr)
                else:
                    DeltaZ = GetCompartment_Thickness(compi)
                StorableMM = (GetSoilLayer_SAT(GetCompartment_Layer(compi)) / 100.0
                              - GetCompartment_theta(compi)) * 1000.0 * DeltaZ \
                             * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
                if StorableMM < 0.0:
                    StorableMM = 0.0
                if StorableMM > amount_still_to_store:
                    SetCompartment_theta(compi, GetCompartment_theta(compi)
                                         + amount_still_to_store
                                         / (1000.0 * GetCompartment_Thickness(compi)
                                            * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)))
                    amount_still_to_store = 0.0
                elif StorableMM > 0.0:
                    SetCompartment_theta(compi, GetCompartment_theta(compi)
                                         + StorableMM
                                         / (1000.0 * GetCompartment_Thickness(compi)
                                            * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)))
                    amount_still_to_store = amount_still_to_store - StorableMM
                compi = compi - 1
                depthi = depthi - GetCompartment_Thickness(compi)
                if (compi == 0) or (amount_still_to_store <= 0.0):
                    break

        # excess
        if amount_still_to_store > 0.0:
            if InfiltratedRain > 0.0:
                InfiltratedRain = InfiltratedRain - amount_still_to_store
            if GetManagement_BundHeight() >= 0.01:
                SetSurfaceStorage(GetSurfaceStorage() + amount_still_to_store)
                if GetSurfaceStorage() > (GetManagement_BundHeight() * 1000.0):
                    SetRunoff(GetRunoff() + (GetSurfaceStorage()
                                             - GetManagement_BundHeight() * 1000.0))
                    SetSurfaceStorage(GetManagement_BundHeight() * 1000.0)
            else:
                SetRunoff(GetRunoff() + amount_still_to_store)
        # STORAGE in Rootzone (= EffecRain)
    # end if

    return InfiltratedRain, InfiltratedIrrigation, InfiltratedStorage, SubDrain




def Calculate_factor(layeri, compi):
    delta_theta_SAT = calculate_delta_theta(GetSoilLayer_SAT(layeri) / 100.0,
                                            GetSoilLayer_FC(layeri) / 100.0,
                                            layeri)
    if delta_theta_SAT > 0.0:
        return GetSoilLayer_InfRate(layeri) / (delta_theta_SAT * 1000.0
                                                * GetCompartment_Thickness(compi)
                                                * (1.0 - GetSoilLayer_GravelVol(layeri) / 100.0))
    else:
        return 1.0


def DetermineCCiGDD(CCxTotal, CCoTotal,
                   StressLeaf, FracAssim, MobilizationON,
                   StorageON, SumGDDAdjCC, VirtualTimeCC,
                   StressSenescence, TimeSenescence, NoMoreCrop,
                   CDCTotal, GDDayFraction,
                   GDDayi, GDDCDCTotal, GDDTadj):
    CCdormant = 0.05

    pLeafLLAct = 0.0
    GDDCGCadjusted = 0.0
    GDDCDCadjusted = 0.0
    CCiSen = 0.0
    GDDtTemp = 0.0
    CCxSF = 0.0
    CGCGDDSF = 0.0
    CCxSFCD = 0.0
    RatDGDD = 0.0
    KsRED = 0.0
    CCibis = 0.0
    GDDtFinalCCx = 0
    WithBeta = False
    TheSenescenceON = False

    KsSen = 0.0
    Crop_pLeafAct_temp = 0.0
    Crop_pSenAct_temp = 0.0
    Crop_CCxAdjusted_temp = 0.0

    if (SumGDDAdjCC <= GetCrop_GDDaysToGermination()) or (roundc(SumGDDAdjCC, mold=1) > GetCrop_GDDaysToHarvest()):
        SetCCiActual(0.0)
    else:
        # growing season (once germinated)
        # 1. find some parameters
        CGCGDDSF = GetCrop_GDDCGC() * (1.0 - GetSimulation_EffectStress_RedCGC() / 100.0)
        GDDCGCadjusted = CGCGDDSF

        RatDGDD = 1.0
        if GetCrop_GDDaysToFullCanopySF() < GetCrop_GDDaysToSenescence():
            RatDGDD = (GetCrop_DaysToSenescence() - GetCrop_DaysToFullCanopySF()) / float(
                GetCrop_GDDaysToSenescence() - GetCrop_GDDaysToFullCanopySF()
            )

        CCxSF = CCxTotal * (1.0 - GetSimulation_EffectStress_RedCCX() / 100.0)
        # maximum canopy cover than can be reached
        # (considering soil fertility/salinity, weed stress)
        if SumGDDAdjCC <= GetCrop_GDDaysToFullCanopySF():
            CCxSFCD = CCxSF  # no canopy decline before max canopy can be reached
        else:
            # canopy decline due to soil fertility
            if SumGDDAdjCC < GetCrop_GDDaysToSenescence():
                CCxSFCD = CCiNoWaterStressSF(
                    (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                    GetCrop_DaysToGermination(),
                    GetCrop_DaysToFullCanopySF(),
                    GetCrop_DaysToSenescence(),
                    GetCrop_DaysToHarvest(),
                    GetCrop_GDDaysToGermination(),
                    GetCrop_GDDaysToFullCanopySF(),
                    GetCrop_GDDaysToSenescence(),
                    GetCrop_GDDaysToHarvest(),
                    CCoTotal, CCxTotal, GetCrop_CGC(),
                    GetCrop_GDDCGC(), CDCTotal, GDDCDCTotal,
                    SumGDDAdjCC, RatDGDD,
                    GetSimulation_EffectStress_RedCGC(),
                    GetSimulation_EffectStress_RedCCX(),
                    GetSimulation_EffectStress_CDecline(),
                    GetCrop_ModeCycle()
                )
            else:
                CCxSFCD = CCxSF - (RatDGDD * GetSimulation_EffectStress_CDecline() / 100.0) * (
                    GetCrop_GDDaysToSenescence() - GetCrop_GDDaysToFullCanopySF()
                )
            if CCxSFCD < 0.0:
                CCxSFCD = 0.0

        StressLeaf = undef_int
        if (abs(SumGDDAdjCC - GetCrop_GDDaysToGermination()) < epsilon(0.0)) and (GetCrop_DaysToCCini() == 0):
            SetCCiPrev(CCoTotal)

        # time of potential vegetative growth
        GDDtFinalCCx = GetCrop_GDDaysToSenescence()  # non determinant crop
        if (GetCrop_subkind() == subkind_Grain) and (GetCrop_DeterminancyLinked()):
            # determinancy
            # reduce GDDtFinalCCx in f(determinancy of crop)
            if GetCrop_DaysToCCini() != 0:
                # regrowth
                GDDtFinalCCx = GetCrop_GDDaysToFullCanopy() + roundc(
                    GDDayFraction * (
                        GetCrop_GDDaysToFlowering()
                        + (GetCrop_GDDLengthFlowering() / 2.0)
                        + GDDTadj
                        + GetCrop_GDDaysToGermination()
                        - GetCrop_GDDaysToFullCanopy()
                    ),
                    mold=1
                )  # slow down
            else:
                # sown or transplant
                GDDtFinalCCx = GetCrop_GDDaysToFlowering() + roundc(GetCrop_GDDLengthFlowering() / 2.0, mold=1)
            if GDDtFinalCCx > GetCrop_GDDaysToSenescence():
                GDDtFinalCCx = GetCrop_GDDaysToSenescence()

        # Crop.pLeafAct and Crop.pSenAct for plotting root zone depletion in RUN
        Crop_pLeafAct_temp = GetCrop_pLeafAct()
        Crop_pLeafAct_temp, pLeafLLAct = AdjustpLeafToETo(GetETo(), Crop_pLeafAct_temp, pLeafLLAct)
        SetCrop_pLeafAct(Crop_pLeafAct_temp)
        WithBeta = True
        Crop_pSenAct_temp = GetCrop_pSenAct()
        Crop_pSenAct_temp = AdjustpSenescenceToETo(GetETo(), TimeSenescence, WithBeta, Crop_pSenAct_temp)
        SetCrop_pSenAct(Crop_pSenAct_temp)

        # 2. Canopy can still develop (stretched to GDDtFinalCCx)
        if SumGDDAdjCC < GDDtFinalCCx:
            # Canopy can stil develop (stretched to GDDtFinalCCx)
            if (GetCCiPrev() <= GetCrop_CCoAdjusted()) or (SumGDDAdjCC <= GDDayi) or (
                (GetSimulation_ProtectedSeedling()) and (GetCCiPrev() <= (1.25 * CCoTotal))
            ):
                # 2.a First day or very small CC as a result of senescence
                # (no adjustment for leaf stress)
                if GetSimulation_ProtectedSeedling():
                    SetCCiActual(CanopyCoverNoStressSF(
                        (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                        GetCrop_DaysToGermination(),
                        GetCrop_DaysToSenescence(),
                        GetCrop_DaysToHarvest(),
                        GetCrop_GDDaysToGermination(),
                        GetCrop_GDDaysToSenescence(),
                        GetCrop_GDDaysToHarvest(),
                        CCoTotal, CCxTotal, GetCrop_CGC(),
                        CDCTotal, GetCrop_GDDCGC(),
                        GDDCDCadjusted, SumGDDAdjCC,
                        GetCrop_ModeCycle(),
                        GetSimulation_EffectStress_RedCGC(),
                        GetSimulation_EffectStress_RedCCX()
                    ))
                    if GetCCiActual() > (1.25 * CCoTotal):
                        SetSimulation_ProtectedSeedling(False)
                else:
                    SetCCiActual(GetCrop_CCoAdjusted() * math.exp(CGCGDDSF * GDDayi))
            # 2.b CC > CCo
            else:
                if GetCCiPrev() < (0.97999 * CCxSF):
                    GDDCGCadjusted = DetermineGDDCGCadjusted(GDDCGCadjusted, CGCGDDSF, pLeafLLAct)
                    StressLeaf = globals().get("StressLeaf", StressLeaf)
                    if GDDCGCadjusted > ac_zero_threshold:
                        # Crop.GDDCGC or GDDCGCadjusted > 0
                        Crop_CCxAdjusted_temp = GetCrop_CCxAdjusted()
                        Crop_CCxAdjusted_temp = DetermineCCxAdjusted_GDD(
                            Crop_CCxAdjusted_temp, CCxSF, GDDCGCadjusted, GDDtFinalCCx, SumGDDAdjCC, GDDayi
                        )
                        SetCrop_CCxAdjusted(Crop_CCxAdjusted_temp)
                        if GetCrop_CCxAdjusted() < 0.0:
                            SetCCiActual(GetCCiPrev())
                        elif abs(GetCCiPrev() - 0.97999 * CCxSF) < 0.001:
                            SetCCiActual(CanopyCoverNoStressSF(
                                (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                                GetCrop_DaysToGermination(),
                                GetCrop_DaysToSenescence(),
                                GetCrop_DaysToHarvest(),
                                GetCrop_GDDaysToGermination(),
                                GetCrop_GDDaysToSenescence(),
                                GetCrop_GDDaysToHarvest(),
                                CCoTotal, CCxTotal, GetCrop_CGC(),
                                CDCTotal, GetCrop_GDDCGC(), GDDCDCadjusted,
                                SumGDDAdjCC, GetCrop_ModeCycle(),
                                GetSimulation_EffectStress_RedCGC(),
                                GetSimulation_EffectStress_RedCCX()
                            ))
                        else:
                            GDDtTemp = RequiredGDD(
                                GetCCiPrev(), GetCrop_CCoAdjusted(), GetCrop_CCxAdjusted(),
                                GDDCGCadjusted, SumGDDAdjCC, GDDayi
                            )
                            if GDDtTemp < 0.0:
                                SetCCiActual(GetCCiPrev())
                            else:
                                GDDtTemp = GDDtTemp + GDDayi
                                SetCCiActual(CCatGDDTime(
                                    GDDtTemp, GetCrop_CCoAdjusted(), GDDCGCadjusted, GetCrop_CCxAdjusted()
                                ))
                    else:
                        # GDDCGCadjusted = 0 - too dry for leaf expansion
                        SetCCiActual(GetCCiPrev())
                        if GetCCiActual() > GetCrop_CCoAdjusted():
                            SetCrop_CCoAdjusted(CCoTotal)
                        else:
                            SetCrop_CCoAdjusted(GetCCiActual())
                else:
                    SetCCiActual(CanopyCoverNoStressSF(
                        (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                        GetCrop_DaysToGermination(),
                        GetCrop_DaysToSenescence(),
                        GetCrop_DaysToHarvest(),
                        GetCrop_GDDaysToGermination(),
                        GetCrop_GDDaysToSenescence(),
                        GetCrop_GDDaysToHarvest(),
                        CCoTotal, CCxTotal, GetCrop_CGC(), CDCTotal,
                        GetCrop_GDDCGC(), GDDCDCadjusted, SumGDDAdjCC,
                        GetCrop_ModeCycle(),
                        GetSimulation_EffectStress_RedCGC(),
                        GetSimulation_EffectStress_RedCCX()
                    ))
                    SetCrop_CCoAdjusted(CCoTotal)
                    StressLeaf = -33.0  # maximum canopy is reached;
                if GetCCiActual() > CCxSFCD:
                    SetCCiActual(CCxSFCD)
                    StressLeaf = -33.0  # maximum canopy is reached;
            SetCrop_CCxAdjusted(GetCCiActual())

        # 3. Canopy can no longer develop
        # (Mid-season (from tFinalCCx) or Late season stage)
        else:
            StressLeaf = -33.0  # maximum canopy is reached;
            if GetCrop_CCxAdjusted() < 0.0:
                SetCrop_CCxAdjusted(GetCCiPrev())

            if SumGDDAdjCC < GetCrop_GDDaysToSenescence():  # mid-season

                SetCCiActual(GetCrop_CCxAdjusted())

                """
                if GetCrop_CCxAdjusted() > 0.97999 * CCxSF:
                    SetCCiActual(CanopyCoverNoStressSF(
                        (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                        GetCrop_DaysToGermination(),
                        GetCrop_DaysToSenescence(),
                        GetCrop_DaysToHarvest(),
                        GetCrop_GDDaysToGermination(),
                        GetCrop_GDDaysToSenescence(),
                        GetCrop_GDDaysToHarvest(),
                        CCoTotal, CCxTotal, GetCrop_CGC(),
                        CDCTotal, GetCrop_GDDCGC(),
                        GDDCDCadjusted, SumGDDAdjCC,
                        GetCrop_ModeCycle(),
                        GetSimulation_EffectStress_RedCGC(),
                        GetSimulation_EffectStress_RedCCX()
                    ))
                    SetCrop_CCxAdjusted(GetCCiActual())
                else:
                    SetCCiActual(CanopyCoverNoStressSF(
                        (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                        GetCrop_DaysToGermination(),
                        GetCrop_DaysToSenescence(),
                        GetCrop_DaysToHarvest(),
                        GetCrop_GDDaysToGermination(),
                        GetCrop_GDDaysToSenescence(),
                        GetCrop_GDDaysToHarvest(),
                        CCoTotal,
                        (GetCrop_CCxAdjusted() / (1.0 - GetSimulation_EffectStress_RedCCX() / 100.0)),
                        GetCrop_CGC(), CDCTotal, GetCrop_GDDCGC(),
                        GDDCDCadjusted, SumGDDAdjCC,
                        GetCrop_ModeCycle(),
                        GetSimulation_EffectStress_RedCGC(),
                        GetSimulation_EffectStress_RedCCX()
                    ))
                """
                if GetCCiActual() > CCxSFCD:
                    SetCCiActual(CCxSFCD)
            # late season
            else:
                StressSenescence = undef_int  # to avoid display of zero stress
                if GetCrop_CCxAdjusted() > CCxSFCD:
                    SetCrop_CCxAdjusted(CCxSFCD)
                if GetCrop_CCxAdjusted() < 0.01:
                    SetCCiActual(0.0)
                else:
                    if GetCrop_GDDaysToSenescence() <= GetCrop_GDDaysToFullCanopySF():
                        CCibis = GetCCiActual()
                    else:
                        CCibis = CCxSF - (RatDGDD * GetSimulation_EffectStress_CDecline() / 100.0) * (
                            math.exp(2.0 * math.log(SumGDDAdjCC - GetCrop_GDDaysToFullCanopySF()))
                            / (GetCrop_GDDaysToSenescence() - GetCrop_GDDaysToFullCanopySF())
                        )
                    if CCibis < 0.0:
                        SetCCiActual(0.0)
                    else:
                        GDDCDCadjusted = GetGDDCDCadjustedNoStress(CCxTotal, GDDCDCTotal, GetCrop_CCxAdjusted())
                        if SumGDDAdjCC < (GetCrop_GDDaysToSenescence() + LengthCanopyDecline(GetCrop_CCxAdjusted(), GDDCDCadjusted)):
                            SetCCiActual(GetCrop_CCxAdjusted() * (
                                1.0 - 0.05 * (
                                    math.exp((SumGDDAdjCC - GetCrop_GDDaysToSenescence()) * 3.33 * GDDCDCadjusted / (GetCrop_CCxAdjusted() + 2.29))
                                    - 1.0
                                )
                            ))
                            if CCibis < GetCCiActual():
                                SetCCiActual(CCibis)
                        else:
                            SetCCiActual(0.0)

        # Adjustment for plant recovery upon rewatering (dormant period) ONLY when crop has still the potential for vegetative
        # growth
        # 4. Canopy senescence due to water stress ?
        if (SumGDDAdjCC < GetCrop_GDDaysToSenescence()) or (TimeSenescence > 0.0):
            StressSenescence = 0.0
            WithBeta = True
            Crop_pSenAct_temp = GetCrop_pSenAct()
            Crop_pSenAct_temp = AdjustpSenescenceToETo(GetETo(), TimeSenescence, WithBeta, Crop_pSenAct_temp)
            SetCrop_pSenAct(Crop_pSenAct_temp)
            KsRED = 1.0
            if GetSimulation_SWCtopSoilConsidered():
                if ((GetRootZoneWC_ZtopAct()
                        < (GetRootZoneWC_ZtopFC()
                            - GetCrop_pSenAct() * KsRED
                                * (GetRootZoneWC_ZtopFC()
                                    - GetRootZoneWC_ZtopWP())))
                    and (GetSimulation_ProtectedSeedling() == False)):
                    TheSenescenceON = True
                else:
                    TheSenescenceON = False
            else:
                if ((GetRootZoneWC_Actual()
                        < (GetRootZoneWC_FC()
                            - GetCrop_pSenAct() * KsRED
                                * (GetRootZoneWC_FC() - GetRootZoneWC_WP())))
                    and (GetSimulation_ProtectedSeedling() == False)):
                    TheSenescenceON = True
                else:
                    TheSenescenceON = False

            if TheSenescenceON:
                SetSimulation_EvapLimitON(True)
                if abs(TimeSenescence) < epsilon(0.0):
                    SetCCiTopEarlySen(GetCCiActual())
                TimeSenescence = TimeSenescence + GDDayi
                GDDCDCadjusted, KsSen, StressSenescence = DetermineGDDCDCadjustedWaterStress(
                    GDDCDCadjusted, KsSen, TimeSenescence, GDDCDCTotal, CCxSFCD, CCxTotal
                )

                if GetCCiTopEarlySen() < 0.001:
                    if ((GetSimulation_SumEToStress() > GetCrop_SumEToDelaySenescence()) or (abs(GetCrop_SumEToDelaySenescence()) < epsilon(0.0)) or (SumGDDAdjCC >= GDDtFinalCCx)):
                        CCiSen = 0.0 # no crop anymore
                    else:
                        if CCdormant > GetCrop_CCo():
                            CCiSen = GetCrop_CCo() + (1.0 - GetSimulation_SumEToStress() / GetCrop_SumEToDelaySenescence()) * (CCdormant - GetCrop_CCo())
                        else:
                            CCiSen = GetCrop_CCo()
                else:
                    if (((TimeSenescence * GDDCDCadjusted * 3.33) / (GetCCiTopEarlySen() + 2.29) > 100.0)
                        or (GetCCiPrev() >= 1.05 * GetCCiTopEarlySen())):
                        if ((GetSimulation_SumEToStress() > GetCrop_SumEToDelaySenescence()) or (abs(GetCrop_SumEToDelaySenescence()) < epsilon(0.0)) or (SumGDDAdjCC >= GDDtFinalCCx)):
                            CCiSen = 0.0 # no crop anymore
                        else:
                            if CCdormant > GetCrop_CCo():
                                CCiSen = GetCrop_CCo() + (1.0 - GetSimulation_SumEToStress() / GetCrop_SumEToDelaySenescence()) * (CCdormant - GetCrop_CCo())
                            else:
                                CCiSen = GetCrop_CCo()
                    else:
                        GDDtTemp = math.log(1.0 + (1.0 - GetCCiPrev() / GetCCiTopEarlySen()) / 0.05) / (
                            GDDCDCadjusted * 3.33 / (GetCCiTopEarlySen() + 2.29)
                        )
                        CCiSen = GetCCiTopEarlySen() * (
                            1.0 - 0.05 * (math.exp((GDDtTemp + GDDayi) * GDDCDCadjusted * 3.33 / (GetCCiTopEarlySen() + 2.29)) - 1.0)
                        )

                    if CCiSen < 0.0:
                        CCiSen = 0.0
                    if ((GetCrop_SumEToDelaySenescence() > 0.0) and (GetSimulation_SumEToStress() <= GetCrop_SumEToDelaySenescence()) and (SumGDDAdjCC < GDDtFinalCCx)):
                        if (CCiSen < GetCrop_CCo()) or (CCiSen < CCdormant):
                            if CCdormant > GetCrop_CCo():
                                CCiSen = GetCrop_CCo() + (1.0 - GetSimulation_SumEToStress() / GetCrop_SumEToDelaySenescence()) * (CCdormant - GetCrop_CCo())
                            else:
                                CCiSen = GetCrop_CCo()

                if SumGDDAdjCC < GetCrop_GDDaysToSenescence():
                    if CCiSen > CCxSFCD:
                        CCiSen = CCxSFCD
                    SetCCiActual(CCiSen)
                    if GetCCiActual() > GetCCiPrev():
                        SetCCiActual(GetCCiPrev())
                    SetCrop_CCxAdjusted(GetCCiActual())
                    if GetCCiActual() < CCoTotal:
                        SetCrop_CCoAdjusted(GetCCiActual())
                    else:
                        SetCrop_CCoAdjusted(CCoTotal)
                else:
                    if CCiSen < GetCCiActual():
                        SetCCiActual(CCiSen)

                if (roundc(10000.0 * CCiSen, mold=1) <= (10000.0 * CCdormant)) or (
                    roundc(10000.0 * CCiSen, mold=1) <= roundc(10000.0 * GetCrop_CCo(), mold=1)
                ):
                    SetSimulation_SumEToStress(GetSimulation_SumEToStress() + GetETo())
            else:
                if (TimeSenescence > 0.0) and (SumGDDAdjCC > GetCrop_GDDaysToSenescence()):
                    Crop_CCxAdjusted_temp = GetCrop_CCxAdjusted()
                    Crop_CCxAdjusted_temp, GDDCDCadjusted = GetNewCCxandGDDCDC(
                        GetCCiPrev(), GDDCDCTotal, CCxSF, Crop_CCxAdjusted_temp, GDDCDCadjusted, SumGDDAdjCC, GDDayi
                    )
                    SetCrop_CCxAdjusted(Crop_CCxAdjusted_temp)
                    SetCCiActual(CanopyCoverNoStressSF(
                        (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                        GetCrop_DaysToGermination(),
                        GetCrop_DaysToSenescence(),
                        GetCrop_DaysToHarvest(),
                        GetCrop_GDDaysToGermination(),
                        GetCrop_GDDaysToSenescence(),
                        GetCrop_GDDaysToHarvest(),
                        CCoTotal,
                        (GetCrop_CCxAdjusted() / (1.0 - GetSimulation_EffectStress_RedCCX() / 100.0)),
                        GetCrop_CGC(), CDCTotal, GetCrop_GDDCGC(),
                        GDDCDCadjusted, SumGDDAdjCC,
                        GetCrop_ModeCycle(),
                        GetSimulation_EffectStress_RedCGC(),
                        GetSimulation_EffectStress_RedCCX()
                    ))
                TimeSenescence = 0.0
                StressSenescence = 0.0
                SetSimulation_SumEToStress(0.0)

        # 5. Adjust Crop.CCxWithered - required for correction
        # of Transpiration of dying green canopy
        if GetCCiActual() > GetCrop_CCxWithered():
            SetCrop_CCxWithered(GetCCiActual())

        # 6. correction for late-season stage for rounding off errors
        if SumGDDAdjCC > GetCrop_GDDaysToSenescence():
            if GetCCiActual() > GetCCiPrev():
                SetCCiActual(GetCCiPrev())

        # 7. no crop as a result of fertiltiy and/or water stress
        if roundc(1000.0 * GetCCiActual(), mold=1) <= 0:
            NoMoreCrop = True

    return StressLeaf, StressSenescence, TimeSenescence, NoMoreCrop


def DetermineCCxAdjusted_GDD(CCxAdjusted, CCxSF, GDDCGCadjusted, GDDtFinalCCx, SumGDDadjCC, GDDayi):
    GDDtfictive = 0.0

    # 1. find time (GDDtfictive) required to reach CCiPrev
    # (CCi of previous day) with GDDCGCadjusted
    GDDtfictive = RequiredGDD(
        GetCCiPrev(),
        GetCrop_CCoAdjusted(),
        CCxSF,
        GDDCGCadjusted,
        SumGDDadjCC,
        GDDayi
    )

    # 2. Get CCxadjusted (reached at end of stretched crop development)
    if GDDtfictive > 0.0:
        GDDtfictive = GDDtfictive + (GDDtFinalCCx - SumGDDadjCC) + GDDayi
        CCxAdjusted = CCatGDDTime(
            GDDtfictive,
            GetCrop_CCoAdjusted(),
            GDDCGCadjusted,
            CCxSF
        )
    else:
        CCxAdjusted = undef_double  # this means CCiActual := CCiPrev

    return CCxAdjusted


def DetermineGDDCGCadjusted(GDDCGCadjusted, CGCGDDSF, pLeafLLAct):
    Wrelative = 0.0
    KsLeaf = 0.0
    SWCeffectiveRootZone = 0.0
    FCeffectiveRootZone = 0.0
    WPeffectiveRootZone = 0.0

    # determine FC and PWP
    if GetSimulation_SWCtopSoilConsidered():
        # top soil is relative wetter than total root zone
        SWCeffectiveRootZone = GetRootZoneWC_ZtopAct()
        Wrelative = (GetRootZoneWC_ZtopFC() - GetRootZoneWC_ZtopAct()) / (GetRootZoneWC_ZtopFC() - GetRootZoneWC_ZtopWP())
        # top soil
        FCeffectiveRootZone = GetRootZoneWC_ZtopFC()
        WPeffectiveRootZone = GetRootZoneWC_ZtopWP()
    else:
        SWCeffectiveRootZone = GetRootZoneWC_Actual()
        Wrelative = (GetRootZoneWC_FC() - GetRootZoneWC_Actual()) / (GetRootZoneWC_FC() - GetRootZoneWC_WP())
        # total root zone
        FCeffectiveRootZone = GetRootZoneWC_FC()
        WPeffectiveRootZone = GetRootZoneWC_WP()

    # Canopy stress and effect of water stress on CGCGDD
    global StressLeaf
    if SWCeffectiveRootZone >= FCeffectiveRootZone:
        GDDCGCadjusted = CGCGDDSF
        StressLeaf = 0.0
    else:
        if SWCeffectiveRootZone <= WPeffectiveRootZone:
            GDDCGCadjusted = 0.0
            StressLeaf = 100.0
        else:
            if Wrelative <= GetCrop_pLeafAct():
                GDDCGCadjusted = CGCGDDSF
                StressLeaf = 0.0
            elif Wrelative >= pLeafLLAct:
                GDDCGCadjusted = 0.0
                StressLeaf = 100.0
            else:
                KsLeaf = KsAny(Wrelative, GetCrop_pLeafAct(), pLeafLLAct, GetCrop_KsShapeFactorLeaf())
                GDDCGCadjusted = CGCGDDSF * KsLeaf
                StressLeaf = 100.0 * (1.0 - KsLeaf)

    return GDDCGCadjusted

def RequiredGDD(CCiToFind, CCo, CCx, GDDCGCadjusted, SumGDDadjCC, GDDayi):
    if CCiToFind <= (CCx / 2.0):
        GDDCGCx = math.log(CCiToFind / CCo) / (SumGDDadjCC - GDDayi)
    else:
        GDDCGCx = math.log((0.25 * CCx * CCx / CCo) / (CCx - CCiToFind)) / (SumGDDadjCC - GDDayi)

    RequiredGDD_val = (SumGDDadjCC - GDDayi) * GDDCGCx / GDDCGCadjusted
    return RequiredGDD_val

def CCatGDDTime(GDDtfictive, CCoGiven, GDDCGCGiven, CCxGiven):
    CCi = CCoGiven * math.exp(GDDCGCGiven * GDDtfictive)
    if CCi > (CCxGiven / 2.0):
        CCi = CCxGiven - 0.25 * (CCxGiven / CCoGiven) * CCxGiven * math.exp(-GDDCGCGiven * GDDtfictive)
    return CCi





def GetGDDCDCadjustedNoStress(CCx, GDDCDC, CCxAdjusted):
    GDDCDCadjusted = GDDCDC * ((CCxAdjusted + 2.29) / (CCx + 2.29))
    return GDDCDCadjusted

def DetermineGDDCDCadjustedWaterStress(GDDCDCadjusted, KsSen, TimeSenescence, GDDCDCTotal, CCxSFCD, CCxTotal):
    Wrelative = 0.0
    pSenLL = 0.0
    pSenAct = 0.0
    WithBeta = False

    pSenLL = 0.999  # WP
    if GetSimulation_SWCtopSoilConsidered():
        # top soil is relative wetter than total root zone
        Wrelative = (GetRootZoneWC_ZtopFC() - GetRootZoneWC_ZtopAct()) / (GetRootZoneWC_ZtopFC() - GetRootZoneWC_ZtopWP())
        # top soil
    else:
        Wrelative = (GetRootZoneWC_FC() - GetRootZoneWC_Actual()) / (GetRootZoneWC_FC() - GetRootZoneWC_WP())
        # total root zone

    WithBeta = False
    pSenAct = AdjustpSenescenceToETo(GetETo(), TimeSenescence, WithBeta, pSenAct)

    if Wrelative <= pSenAct:
        GDDCDCadjusted = 0.0001  # extreme small decline
        StressSenescence = 0.0
        KsSen = 1.0
    elif Wrelative >= pSenLL:
        GDDCDCadjusted = GDDCDCTotal * ((CCxSFCD + 2.29) / (CCxTotal + 2.29))
        # full speed
        StressSenescence = 100.0
        KsSen = 0.0
    else:
        KsSen = KsAny(Wrelative, pSenAct, pSenLL, GetCrop_KsShapeFactorSenescence())
        if KsSen > ac_zero_threshold:
            GDDCDCadjusted = GDDCDCTotal * ((CCxSFCD + 2.29) / (CCxTotal + 2.29)) * (1.0 - math.exp(8.0 * math.log(KsSen)))
            StressSenescence = 100.0 * (1.0 - KsSen)
        else:
            GDDCDCadjusted = 0.0001  # extreme small decline
            StressSenescence = 0.0

    return GDDCDCadjusted, KsSen, StressSenescence

def GetNewCCxandGDDCDC(CCiPrev, GDDCDC, CCx, CCxAdjusted, GDDCDCadjusted, SumGDDadjCC, GDDayi):
    CCxAdjusted = CCiPrev / (
        1.0
        - 0.05
        * (
            math.exp(
                (SumGDDadjCC - GDDayi - GetCrop_GDDaysToSenescence())
                * GDDCDC
                * 3.33
                / (CCx + 2.29)
            )
            - 1.0
        )
    )
    GDDCDCadjusted = GDDCDC * (CCxAdjusted + 2.29) / (CCx + 2.29)
    return CCxAdjusted, GDDCDCadjusted

def NoEffectStress(TheEffectStress):
        TheEffectStress.RedCGC = 0.0
        TheEffectStress.RedCCX = 0.0
        TheEffectStress.RedWP = 0.0
        TheEffectStress.CDecline = 0.0
        TheEffectStress.RedKsSto = 0.0

def EffectSoilFertilitySalinityStress(StressSFadjNEW, Coeffb0Salt,
                                      Coeffb1Salt, Coeffb2Salt,
                                      NrDayGrow, StressTotSaltPrev,
                                      VirtualTimeCC):

    FertilityEffectStress = rep_EffectStress()
    SalinityEffectStress = rep_EffectStress()
    SaltStress = 0.0
    CCxRedD = 0.0
    CCxRed = 0
    ECe_temp = 0.0
    ECsw_temp = 0.0
    ECswFC_temp = 0.0
    KsSalt_temp = 0.0
    RedCGC_temp = 0
    RedCCX_temp = 0
    Crop_DaysToFullCanopySF_temp = 0
    EffectStress_temp = rep_EffectStress()

    if GetSimulation_SalinityConsidered():
        ECe_temp = GetRootZoneSalt_ECe()
        ECsw_temp = GetRootZoneSalt_ECsw()
        ECswFC_temp = GetRootZoneSalt_ECswFC()
        KsSalt_temp = GetRootZoneSalt_KsSalt()
        ECe_temp, ECsw_temp, ECswFC_temp, KsSalt_temp = DetermineRootZoneSaltContent(
            GetRootingDepth(),
            ECe_temp, ECsw_temp,
            ECswFC_temp, KsSalt_temp
        )
        SetRootZoneSalt_ECe(ECe_temp)
        SetRootZoneSalt_ECsw(ECsw_temp)
        SetRootZoneSalt_ECswFC(ECswFC_temp)
        SetRootZoneSalt_KsSalt(KsSalt_temp)
        SaltStress = (NrDayGrow * StressTotSaltPrev + 100.0 * (1.0 - GetRootZoneSalt_KsSalt())) / (NrDayGrow + 1.0)
    else:
        SaltStress = 0.0

    if ((VirtualTimeCC < GetCrop_DaysToGermination())
            or (VirtualTimeCC > (GetCrop_DayN() - GetCrop_Day1()))
            or (GetSimulation_Germinate() == False)
            or ((StressSFadjNEW == 0) and (SaltStress <= 0.1))):
        # no soil fertility and salinity stress
        EffectStress_temp = GetSimulation_EffectStress()
        NoEffectStress(EffectStress_temp)
        SetSimulation_EffectStress(EffectStress_temp)
        SetCrop_DaysToFullCanopySF(GetCrop_DaysToFullCanopy())
        if GetCrop_ModeCycle() == ModeCycle_GDDays:
            SetCrop_GDDaysToFullCanopySF(GetCrop_GDDaysToFullCanopy())
    else:
        # Soil fertility
        if StressSFadjNEW == 0:
            NoEffectStress(FertilityEffectStress)
        else:
            FertilityEffectStress = CropStressParametersSoilFertility(GetCrop_StressResponse(),
                                              StressSFadjNEW,
                                              FertilityEffectStress)
        # Soil Salinity
        CCxRedD = float(roundc(Coeffb0Salt + Coeffb1Salt * SaltStress
                               + Coeffb2Salt * SaltStress * SaltStress,
                               mold=1))
        if ((CCxRedD < 0.0)
                or (SaltStress <= 0.1)
                or (GetSimulation_SalinityConsidered() == False)):
            NoEffectStress(SalinityEffectStress)
        else:
            if (CCxRedD > 100.0) or (SaltStress >= 99.9):
                CCxRed = 100
            else:
                CCxRed = roundc(CCxRedD, mold=1)
            CropStressParametersSoilSalinity(
                CCxRed,
                GetCrop_CCsaltDistortion(),
                GetCrop_CCo(),
                GetCrop_CCx(),
                GetCrop_CGC(),
                GetCrop_GDDCGC(),
                GetCrop_DeterminancyLinked(),
                GetCrop_DaysToFullCanopy(),
                GetCrop_DaysToFlowering(),
                GetCrop_LengthFlowering(),
                GetCrop_DaysToHarvest(),
                GetCrop_GDDaysToFullCanopy(),
                GetCrop_GDDaysToFlowering(),
                GetCrop_GDDLengthFlowering(),
                GetCrop_GDDaysToHarvest(),
                GetCrop_ModeCycle(),
                SalinityEffectStress
            )

        # Assign integrated effect of the stresses
        SetSimulation_EffectStress_RedWP(FertilityEffectStress.RedWP)
        SetSimulation_EffectStress_RedKsSto(SalinityEffectStress.RedKsSto)
        if FertilityEffectStress.RedCGC > SalinityEffectStress.RedCGC:
            SetSimulation_EffectStress_RedCGC(FertilityEffectStress.RedCGC)
        else:
            SetSimulation_EffectStress_RedCGC(SalinityEffectStress.RedCGC)
        if FertilityEffectStress.RedCCX > SalinityEffectStress.RedCCX:
            SetSimulation_EffectStress_RedCCX(FertilityEffectStress.RedCCX)
        else:
            SetSimulation_EffectStress_RedCCX(SalinityEffectStress.RedCCX)
        if FertilityEffectStress.CDecline > SalinityEffectStress.CDecline:
            SetSimulation_EffectStress_CDecline(FertilityEffectStress.CDecline)
        else:
            SetSimulation_EffectStress_CDecline(SalinityEffectStress.CDecline)

        # adjust time to maximum canopy cover
        RedCGC_temp = GetSimulation_EffectStress_RedCGC()
        RedCCX_temp = GetSimulation_EffectStress_RedCCX()
        Crop_DaysToFullCanopySF_temp = GetCrop_DaysToFullCanopySF()
        Crop_DaysToFullCanopySF_temp, RedCGC_temp, RedCCX_temp, StressSFadjNEW = TimeToMaxCanopySF(
            GetCrop_CCo(), GetCrop_CGC(), GetCrop_CCx(),
            GetCrop_DaysToGermination(),
            GetCrop_DaysToFullCanopy(),
            GetCrop_DaysToSenescence(),
            GetCrop_DaysToFlowering(),
            GetCrop_LengthFlowering(),
            GetCrop_DeterminancyLinked(),
            Crop_DaysToFullCanopySF_temp, RedCGC_temp,
            RedCCX_temp, StressSFadjNEW
        )
        SetSimulation_EffectStress_RedCGC(RedCGC_temp)
        SetSimulation_EffectStress_RedCCX(RedCCX_temp)
        SetCrop_DaysToFullCanopySF(Crop_DaysToFullCanopySF_temp)

        if GetCrop_ModeCycle() == ModeCycle_GDDays:
            if ((abs(GetManagement_FertilityStress()) > epsilon(0.0))
                    or (abs(SaltStress) > epsilon(0.0))):
                SetCrop_GDDaysToFullCanopySF(
                    GrowingDegreeDays(
                        GetCrop_DaysToFullCanopySF(),
                        GetCrop_Day1(),
                        GetCrop_Tbase(),
                        GetCrop_Tupper(),
                        GetSimulParam_Tmin(),
                        GetSimulParam_Tmax()
                    )
                )
            else:
                SetCrop_GDDaysToFullCanopySF(GetCrop_GDDaysToFullCanopy())

    return StressSFadjNEW


def PrepareStage1():
    Soil_temp = 0
    Soil_temp = GetSoil()
    if GetSurfaceStorage() > ac_zero_threshold:
        SetSimulation_EvapWCsurf(Soil_temp.REW*1.)
    else:
        SetSimulation_EvapWCsurf(GetRain() + GetIrrigation() - GetRunoff())
        if GetSimulation_EvapWCsurf() > Soil_temp.REW:
            SetSimulation_EvapWCsurf(Soil_temp.REW*1.)
    SetSimulation_EvapStartStg2(int(undef_int))
    SetSimulation_EvapZ(EvapZmin/100.)

def WCEvapLayer(Zlayer, AtTheta):
    Ztot = 0.0
    Wx = 0.0
    fracZ = 0.0
    compi = 0

    while (abs(Zlayer - Ztot) > 0.0001) and (compi < GetNrCompartments()):
        compi = compi + 1
        if (Ztot + GetCompartment_Thickness(compi)) > Zlayer:
            fracZ = (Zlayer - Ztot) / GetCompartment_Thickness(compi)
        else:
            fracZ = 1.0

        if AtTheta == whichtheta_AtSat:
            Wx = Wx + 10.0 \
                    * GetSoilLayer_SAT(GetCompartment_Layer(compi)) \
                    * fracZ * GetCompartment_Thickness(compi) \
                    * (1.0
                        - GetSoilLayer_GravelVol(GetCompartment_Layer(compi))
                                                                    / 100.0)
        elif AtTheta == whichtheta_AtFC:
            Wx = Wx + 10.0 \
                    * GetSoilLayer_FC(GetCompartment_Layer(compi)) \
                    * fracZ * GetCompartment_Thickness(compi) \
                    * (1.0
                        - GetSoilLayer_GravelVol(GetCompartment_Layer(compi))
                                                                    / 100.0)
        elif AtTheta == whichtheta_AtWP:
            Wx = Wx + 10.0 \
                    * GetSoilLayer_WP(GetCompartment_Layer(compi)) \
                    * fracZ * GetCompartment_Thickness(compi) \
                    * (1.0
                        - GetSoilLayer_GravelVol(GetCompartment_Layer(compi))
                                                                    / 100.0)
        else:
            Wx = Wx + 1000.0 \
                    * GetCompartment_theta(compi) * fracZ \
                    * GetCompartment_Thickness(compi) \
                    * (1.0
                        - GetSoilLayer_GravelVol(GetCompartment_Layer(compi))
                                                                        / 100.0)

        Ztot = Ztot + fracZ * GetCompartment_Thickness(compi)

    return Wx

def PrepareStage2():
    AtTheta = 0
    EvapStartStg2 = 0
    WSAT = 0
    WFC = 0
    Wact = 0
    SetSimulation_EvapZ(EvapZmin/100)
    AtTheta = whichtheta_AtSat
    WSAT = WCEvapLayer(GetSimulation_EvapZ(), AtTheta)
    AtTheta = whichtheta_AtFC
    WFC = WCEvapLayer(GetSimulation_EvapZ(), AtTheta)
    AtTheta = whichtheta_AtAct
    Wact = WCEvapLayer(GetSimulation_EvapZ(), AtTheta)
    if (Wact - (WFC-GetSoil_REW())) <= 0.0:
        EvapStartStg2 = 0
    else:
        EvapStartStg2 = roundc(100. * (Wact - (WFC-GetSoil_REW())) / (WSAT - (WFC-GetSoil_REW())), mold=1)
    if EvapStartStg2 < 0:
        EvapStartStg2 = 0
    SetSimulation_EvapStartStg2(EvapStartStg2)


def CalculateEvaporationSurfaceWater():
    SaltSurface = 0
    if GetSurfaceStorage() > GetEpot():
        SaltSurface = GetSurfaceStorage()*GetECstorage()*Equiv
        SetEact(GetEpot())
        SetSurfaceStorage(GetSurfaceStorage() - GetEact())
        SetECstorage(SaltSurface/(GetSurfaceStorage()*Equiv))
        # salinisation of surface storage layer
    else:
        SetEact(GetSurfaceStorage())
        SetSurfaceStorage(0.)
        SetSimulation_EvapWCsurf(float(GetSoil_REW()))
        SetSimulation_EvapZ(EvapZmin/100.)
        if GetSimulation_EvapWCsurf() < 0.0001:
            PrepareStage2()
        else:
            SetSimulation_EvapStartStg2(int(undef_int))


def AdjustEpotMulchWettedSurface(dayi, EpotTot, Epot, EvapWCsurface):
    EpotIrri = 0.0

    # 1. Mulches (reduction of EpotTot to Epot)
    if GetSurfaceStorage() <= ac_zero_threshold:
        if dayi < GetCrop_Day1():  # before season
            Epot = EpotTot * (1.0 - (GetManagement_EffectMulchOffS() / 100.0)
                              * (GetManagement_SoilCoverBefore() / 100.0))
        else:
            if dayi < GetCrop_Day1() + GetCrop_DaysToHarvest():  # in season
                Epot = EpotTot * (1.0
                                  - (GetManagement_EffectMulchInS() / 100.0)
                                  * (GetManagement_Mulch() / 100.0))
            else:
                Epot = EpotTot * (1.0
                                  - (GetManagement_EffectMulchOffS() / 100.0)
                                  * (GetManagement_SoilCoverAfter() / 100.0))
    else:
        Epot = EpotTot  # flooded soil surface

    # 2a. Entire soil surface wetted ?
    if GetIrrigation() > 0.0:
        # before season
        if (dayi < GetCrop_Day1()) and (GetSimulParam_IrriFwOffSeason() < 100):
            SetEvapoEntireSoilSurface(False)
        # in season
        if (dayi >= GetCrop_Day1()) and (dayi < GetCrop_Day1() + GetCrop_DaysToHarvest()) and (GetSimulParam_IrriFwInSeason() < 100):
            SetEvapoEntireSoilSurface(False)
        # after season
        if (dayi >= GetCrop_Day1() + GetCrop_DaysToHarvest()) and (GetSimulParam_IrriFwOffSeason() < 100):
            SetEvapoEntireSoilSurface(False)

    if (GetRain() > 1.0) or (GetSurfaceStorage() > 0.0):
        SetEvapoEntireSoilSurface(True)

    if (dayi >= GetCrop_Day1()) and (dayi < GetCrop_Day1() + GetCrop_DaysToHarvest()) and (GetIrriMode() == IrriMode_Inet):
        SetEvapoEntireSoilSurface(True)

    # 2b. Correction for Wetted surface by Irrigation
    if not GetEvapoEntireSoilSurface():
        if (dayi >= GetCrop_Day1()) and (dayi < GetCrop_Day1() + GetCrop_DaysToHarvest()):
            # in season
            EvapWCsurface = EvapWCsurface * (GetSimulParam_IrriFwInSeason() / 100.0)
            EpotIrri = EpotTot * (GetSimulParam_IrriFwInSeason() / 100.0)
        else:
            # off-season
            EvapWCsurface = EvapWCsurface * (GetSimulParam_IrriFwOffSeason() / 100.0)
            EpotIrri = EpotTot * (GetSimulParam_IrriFwOffSeason() / 100.0)

        if GetEact() > EpotIrri:
            EpotIrri = GetEact()  # Eact refers to the previous day

        if EpotIrri < Epot:
            Epot = EpotIrri

    return Epot, EvapWCsurface


def ConcentrateSalts():
    compi = 0
    celWet = 0
    celi = 0
    SaltTot = 0.0
    mm = 0.0
    Salt_temp = 0.0
    Depo_temp = 0.0

    for compi in range(1, int(GetNrCompartments()) + 1):
        SaltTot = 0.0
        celWet = ActiveCells(GetCompartment_i(compi))
        if celWet < GetSoilLayer_SCP1(GetCompartment_Layer(compi)):
            for celi in range(int(celWet + 1), int(GetSoilLayer_SCP1(GetCompartment_Layer(compi))) + 1):
                SaltTot = SaltTot + GetCompartment_Salt(compi, celi) + GetCompartment_Depo(compi, celi)
                SetCompartment_Salt(compi, celi, 0.0)
                SetCompartment_Depo(compi, celi, 0.0)

        if SaltTot > 0.0:
            SetCompartment_Salt(compi, celWet, GetCompartment_Salt(compi, celWet) + SaltTot)
            mm = (GetSoilLayer_Dx(GetCompartment_Layer(compi)) * 1000.0
                  * GetCompartment_Thickness(compi)
                  * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0))
            Salt_temp = GetCompartment_Salt(compi, celWet)
            Depo_temp = GetCompartment_Depo(compi, celWet)
            Salt_temp, Depo_temp = SaltSolutionDeposit(mm, Salt_temp, Depo_temp)
            SetCompartment_Salt(compi, celWet, Salt_temp)
            SetCompartment_Depo(compi, celWet, Depo_temp)

def ExtractWaterFromEvapLayer(EvapToLose, Zact, Stg1):
    EvapLost = 0.0
    Wx = 0.0
    Wairdry = 0.0
    AvailableW = 0.0
    Ztot = 0.0
    fracZ = 0.0
    StillToExtract = 0.0
    compi = 0

    EvapLost = 0.0
    compi = 0
    Ztot = 0.0
    while True:
        compi = compi + 1
        if (Ztot + GetCompartment_Thickness(compi)) > Zact:
            fracZ = (Zact - Ztot) / GetCompartment_Thickness(compi)
        else:
            fracZ = 1.0
        Wairdry = 10.0 * GetSoilLayer_WP(GetCompartment_Layer(compi)) / 2.0 \
                  * GetCompartment_Thickness(compi) \
                  * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
        Wx = 1000.0 * GetCompartment_theta(compi) \
             * GetCompartment_Thickness(compi) \
             * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0)
        AvailableW = (Wx - Wairdry) * fracZ
        StillToExtract = (EvapToLose - EvapLost)
        if AvailableW > 0.0:
            if AvailableW > StillToExtract:
                SetEact(GetEact() + StillToExtract)
                EvapLost = EvapLost + StillToExtract
                Wx = Wx - StillToExtract
            else:
                SetEact(GetEact() + AvailableW)
                EvapLost = EvapLost + AvailableW
                Wx = Wx - AvailableW
            SetCompartment_theta(
                compi,
                Wx / (1000.0 * GetCompartment_Thickness(compi)
                      * (1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0))
            )
        Ztot = Ztot + fracZ * (GetCompartment_Thickness(compi))
        if (compi >= GetNrCompartments()) or (abs(StillToExtract) < ac_zero_threshold) or (Ztot >= 0.999999 * Zact):
            break

    if Stg1:
        SetSimulation_EvapWCsurf(GetSimulation_EvapWCsurf() - EvapLost)
        if abs(EvapToLose - EvapLost) > 0.0001:
            # not enough water left in the compartment to store WCsurf
            SetSimulation_EvapWCsurf(0.0)



def CalculateSoilEvaporationStage1():
    Eremaining = 0
    Stg1 = True
    Eremaining = GetEpot() - GetEact()
    if GetSimulation_EvapWCsurf() > Eremaining:
        ExtractWaterFromEvapLayer(Eremaining, EvapZmin, Stg1)
    else:
        ExtractWaterFromEvapLayer(GetSimulation_EvapWCsurf(), EvapZmin, Stg1)
    if GetSimulation_EvapWCsurf() < ac_zero_threshold:
        PrepareStage2()


def CalculateSoilEvaporationStage2():
    NrOfStepsInDay = 20
    FractionWtoExpandZ = 0.4
    AtTheta = 0
    Wupper = 0.0
    Wlower = 0.0
    Wact = 0.0
    Eremaining = 0.0
    Wrel = 0.0
    Kr = 0.0
    Elost = 0.0
    MaxSaltExDepth = 0.0
    SX = 0.0
    Zi = 0.0
    SaltDisplaced = 0.0
    UL = 0.0
    DeltaX = 0.0
    i = 0
    compi = 0
    SCell1 = 0
    SCellEnd = 0
    Stg1 = False
    BoolCell = False
    ThetaIniEvap = [0.0 for _ in range(11)]
    SCellIniEvap = [0 for _ in range(11)]

    # Step 1. Conditions before soil evaporation
    compi = 1
    MaxSaltExDepth = GetCompartment_Thickness(1)
    while (MaxSaltExDepth < GetSimulParam_EvapZmax()) and (compi < GetNrCompartments()):
        compi = compi + 1
        i0 = (compi - 1) - 1
        ThetaIniEvap[i0] = GetCompartment_theta(compi)
        SCellIniEvap[i0] = ActiveCells(GetCompartment_i(compi))
        MaxSaltExDepth = MaxSaltExDepth + GetCompartment_Thickness(compi)

    # Step 2. Soil evaporation
    Stg1 = False
    Eremaining = GetEpot() - GetEact()
    Wupper, Wlower = GetLimitsEvapLayer(float(GetSimulation_EvapStartStg2()), Wupper, Wlower)
    for i in range(1, NrOfStepsInDay + 1):
        AtTheta = whichtheta_AtAct
        Wact = WCEvapLayer(GetSimulation_EvapZ(), AtTheta)
        Wrel = (Wact - Wlower) / (Wupper - Wlower)
        if GetSimulParam_EvapZmax() > EvapZmin:
            while ((Wrel < (FractionWtoExpandZ
                * (GetSimulParam_EvapZmax()
                    - (100.0 * GetSimulation_EvapZ()))
                        / (GetSimulParam_EvapZmax() - EvapZmin)))
                and (GetSimulation_EvapZ()
                            < GetSimulParam_EvapZmax() / 100.0)):
                SetSimulation_EvapZ(GetSimulation_EvapZ() + 0.001)
                                                                # add 1 mm
                Wupper, Wlower = GetLimitsEvapLayer(float(GetSimulation_EvapStartStg2()), Wupper, Wlower)
                AtTheta = whichtheta_AtAct
                Wact = WCEvapLayer(GetSimulation_EvapZ(), AtTheta)
                Wrel = (Wact - Wlower) / (Wupper - Wlower)
        Kr = SoilEvaporationReductionCoefficient(Wrel, float(GetSimulParam_EvapDeclineFactor()))
        if abs(GetETo() - 5.0) > 0.01:
            # correction for evaporative demand
            # adjustment of Kr (not considered yet)
            pass
        Elost = Kr * (Eremaining / NrOfStepsInDay)
        ExtractWaterFromEvapLayer(Elost, GetSimulation_EvapZ(), Stg1)

    # Step 3. Upward salt transport
    SX = SaltTransportFactor(GetCompartment_theta(1))
    if SX > 0.01:
        SCell1 = ActiveCells(GetCompartment_i(1))
        compi = 2
        Zi = GetCompartment_Thickness(1) + GetCompartment_Thickness(2)
        while ((roundc(Zi * 100.0, mold=1)
                    <= roundc(MaxSaltExDepth * 100.0, mold=1))
            and (compi <= GetNrCompartments())
            and (roundc(ThetaIniEvap[(compi - 1) - 1] * 100000.0, mold=1)
                    != roundc(GetCompartment_theta(compi) * 100000.0, mold=1))):
            # move salt to compartment 1
            SCellEnd = ActiveCells(GetCompartment_i(compi))
            BoolCell = False
            UL = GetSoilLayer_UL(GetCompartment_Layer(compi))
            DeltaX = GetSoilLayer_Dx(GetCompartment_Layer(compi))
            while True:
                idx_m1 = (compi - 1) - 1
                if SCellEnd < SCellIniEvap[idx_m1]:
                    SaltDisplaced = SX * GetCompartment_Salt(compi, SCellIniEvap[idx_m1])
                    SetCompartment_Salt(
                        compi, SCellIniEvap[idx_m1],
                        GetCompartment_Salt(compi, SCellIniEvap[idx_m1]) - SaltDisplaced
                    )
                    SCellIniEvap[idx_m1] = SCellIniEvap[idx_m1] - 1
                    ThetaIniEvap[idx_m1] = DeltaX * SCellIniEvap[idx_m1]
                else:
                    BoolCell = True
                    if SCellEnd == GetSoilLayer_SCP1(GetCompartment_Layer(compi)):
                        SaltDisplaced = SX * GetCompartment_Salt(compi, SCellIniEvap[idx_m1]) * (
                            ThetaIniEvap[idx_m1] - GetCompartment_theta(compi)
                        ) / (ThetaIniEvap[idx_m1] - UL)
                    else:
                        SaltDisplaced = SX * GetCompartment_Salt(compi, SCellIniEvap[idx_m1]) * (
                            ThetaIniEvap[idx_m1] - GetCompartment_theta(compi)
                        ) / (ThetaIniEvap[idx_m1] - (DeltaX * (SCellEnd - 1)))
                    SetCompartment_Salt(
                        compi, SCellIniEvap[idx_m1],
                        GetCompartment_Salt(compi, SCellIniEvap[idx_m1]) - SaltDisplaced
                    )
                SetCompartment_Salt(1, SCell1, GetCompartment_Salt(1, SCell1) + SaltDisplaced)
                if BoolCell:
                    break
            compi = compi + 1
            if compi <= GetNrCompartments():
                Zi = Zi + GetCompartment_Thickness(compi)
    

def GetLimitsEvapLayer(xProc, Wupper, Wlower):
    AtTheta = 0
    WSAT = 0.0
    WFC = 0.0

    AtTheta = whichtheta_AtSat
    WSAT = WCEvapLayer(GetSimulation_EvapZ(), AtTheta)
    AtTheta = whichtheta_AtFC
    WFC = WCEvapLayer(GetSimulation_EvapZ(), AtTheta)
    Wupper = (xProc / 100.0) * (WSAT - (WFC - GetSoil_REW())) + (WFC - GetSoil_REW())
    AtTheta = whichtheta_AtWP
    Wlower = WCEvapLayer(GetSimulation_EvapZ(), AtTheta) / 2.0

    return Wupper, Wlower

def SaltTransportFactor(theta):
    x = 0.0

    if theta <= GetSoilLayer_WP(1) / 200.0:
        return 0.0
    else:
        x = (theta * 100.0 - GetSoilLayer_WP(1) / 2.0) / (GetSoilLayer_SAT(1) - GetSoilLayer_WP(1) / 2.0)
        return math.exp(x * math.log(10.0) + math.log(x / 10.0))

def DetermineCCxAdjusted_Days(CCxAdjusted, CCxSF, CGCadjusted, tFinalCCx, VirtualTimeCC):
    tfictive = 0.0

    # 1. find time (tfictive) required to reach CCiPrev
    #    (CCi of previous day) with CGCadjusted
    tfictive = RequiredTimeNew(
        GetCCiPrev(),
        GetCrop_CCoAdjusted(),
        CCxSF,
        CGCadjusted
    )

    # 2. Get CCxadjusted (reached at end of stretched crop development)
    if tfictive > 0.0:
        tfictive = tfictive + (tFinalCCx - VirtualTimeCC)
        CCxAdjusted = CCatTime(
            tfictive,
            GetCrop_CCoAdjusted(),
            CGCadjusted,
            CCxSF
        )
    else:
        CCxAdjusted = undef_double  # this means CCiActual := CCiPrev

    return CCxAdjusted

def DetermineCCi(CCxTotal, CCoTotal, StressLeaf, FracAssim,
                 MobilizationON, StorageON, Tadj, VirtualTimeCC,
                 StressSenescence, TimeSenescence, NoMoreCrop,
                 CDCTotal, DayFraction,
                 GDDCDCTotal, TESTVAL):

    CCdormant = 0.05
    pLeafLLAct = 0.0
    CGCadjusted = 0.0
    CDCadjusted = 0.0
    CCiSen = 0.0
    tTemp = 0.0
    CCxSF = 0.0
    CGCSF = 0.0
    CCxSFCD = 0.0
    KsRED = 0.0
    CCibis = 0.0
    tFinalCCx = 0
    WithBeta = False
    TheSenescenceON = False
    KsSen = 0.0
    Crop_pLeafAct_temp = 0.0
    Crop_pSenAct_temp = 0.0
    Crop_CCxAdjusted_temp = 0.0

    # DetermineCCi
    if (VirtualTimeCC < GetCrop_DaysToGermination()) or (VirtualTimeCC > (GetCrop_DayN() - GetCrop_Day1())):
        SetCCiActual(0.0)
    else:
        # growing season (once germinated)
        # 1. find some parameters
        CGCSF = GetCrop_CGC() * (1.0 - GetSimulation_EffectStress_RedCGC() / 100.0)
        CGCadjusted = CGCSF
        CCxSF = CCxTotal * (1.0 - GetSimulation_EffectStress_RedCCX() / 100.0)

        # maximum canopy cover than can be reached
        # (considering soil fertility/salinity, weed stress)
        if VirtualTimeCC <= GetCrop_DaysToFullCanopySF():
            CCxSFCD = CCxSF  # no correction before maximum canopy is reached
        else:
            if VirtualTimeCC < GetCrop_DaysToSenescence():
                CCxSFCD = CCiNoWaterStressSF(
                    (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                    GetCrop_DaysToGermination(),
                    GetCrop_DaysToFullCanopySF(),
                    GetCrop_DaysToSenescence(),
                    GetCrop_DaysToHarvest(),
                    GetCrop_GDDaysToGermination(),
                    GetCrop_GDDaysToFullCanopySF(),
                    GetCrop_GDDaysToSenescence(),
                    GetCrop_GDDaysToHarvest(),
                    CCoTotal, CCxTotal, GetCrop_CGC(),
                    GetCrop_GDDCGC(), CDCTotal, GDDCDCTotal,
                    GetSimulation_SumGDD(), 1.0,
                    GetSimulation_EffectStress_RedCGC(),
                    GetSimulation_EffectStress_RedCCX(),
                    GetSimulation_EffectStress_CDecline(),
                    GetCrop_ModeCycle()
                )
            else:
                CCxSFCD = CCxSF - (GetSimulation_EffectStress_CDecline() / 100.0) * (
                    GetCrop_DaysToSenescence() - GetCrop_DaysToFullCanopySF()
                )
            if CCxSFCD < 0.0:
                CCxSFCD = 0.0

        StressLeaf = undef_int
        if VirtualTimeCC == GetCrop_DaysToGermination():
            SetCCiPrev(CCoTotal)

        # time of potentional vegetative growth
        tFinalCCx = GetCrop_DaysToSenescence()  # undeterminant crop
        if (GetCrop_subkind() == subkind_Grain) and (GetCrop_DeterminancyLinked()):
            # determinant crop
            # reduce tFinalCC in f(determinancy of crop)
            if GetCrop_DaysToCCini() != 0:
                # regrowth  (adjust to slower time)
                tFinalCCx = GetCrop_DaysToFullCanopy() + roundc(
                    DayFraction * (
                        ((GetCrop_DaysToFlowering() + (GetCrop_LengthFlowering() / 2.0) - GetSimulation_DelayedDays()))
                        + Tadj + GetCrop_DaysToGermination() - GetCrop_DaysToFullCanopy()
                    ),
                    mold=1
                )
            else:
                # sown or transplant
                tFinalCCx = GetCrop_DaysToFlowering() + roundc(GetCrop_LengthFlowering() / 2.0, mold=1)
            if tFinalCCx > GetCrop_DaysToSenescence():
                tFinalCCx = GetCrop_DaysToSenescence()

        # Crop.pLeafAct and Crop.pSenAct for
        # plotting root zone depletion in RUN
        Crop_pLeafAct_temp = GetCrop_pLeafAct()
        Crop_pLeafAct_temp, pLeafLLAct = AdjustpLeafToETo(GetETo(), Crop_pLeafAct_temp, pLeafLLAct)
        SetCrop_pLeafAct(Crop_pLeafAct_temp)
        WithBeta = True
        Crop_pSenAct_temp = GetCrop_pSenAct()
        Crop_pSenAct_temp = AdjustpSenescenceToETo(GetETo(), TimeSenescence, WithBeta, Crop_pSenAct_temp)
        SetCrop_pSenAct(Crop_pSenAct_temp)

        # 2. Canopy can still develop (stretched to tFinalCCx)
        if VirtualTimeCC < tFinalCCx:
            # Canopy can stil develop (stretched to tFinalCCx)
            if (GetCCiPrev() <= GetCrop_CCoAdjusted()) or (VirtualTimeCC <= 1) or (
                (GetSimulation_ProtectedSeedling()) and (GetCCiPrev() <= (1.25 * CCoTotal))
            ):
                # 2.a first day or very small CC as a result of senescence
                # (no adjustment for leaf stress)
                if GetSimulation_ProtectedSeedling():
                    SetCCiActual(CanopyCoverNoStressSF(
                        (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                        GetCrop_DaysToGermination(),
                        GetCrop_DaysToSenescence(),
                        GetCrop_DaysToHarvest(),
                        GetCrop_GDDaysToGermination(),
                        GetCrop_GDDaysToSenescence(),
                        GetCrop_GDDaysToHarvest(),
                        CCoTotal, CCxTotal, GetCrop_CGC(),
                        CDCTotal, GetCrop_GDDCGC(), GDDCDCTotal,
                        GetSimulation_SumGDD(), GetCrop_ModeCycle(),
                        GetSimulation_EffectStress_RedCGC(),
                        GetSimulation_EffectStress_RedCCX()
                    ))
                    if GetCCiActual() > (1.25 * CCoTotal):
                        SetSimulation_ProtectedSeedling(False)
                else:
                    # this results in CC increase when during senescence CC
                    # becomes smaller than CCini)
                    if VirtualTimeCC == 1:
                        SetCCiActual(GetCrop_CCoAdjusted() * math.exp(CGCSF * 2.0))
                    else:
                        SetCCiActual(GetCrop_CCoAdjusted() * math.exp(CGCSF * 1.0))

            # 2.b CC > CCo
            else:
                if GetCCiPrev() < 0.97999 * CCxSF:
                    CGCadjusted = DetermineCGCadjusted(CGCadjusted, CGCSF, pLeafLLAct)
                    StressLeaf = globals().get("StressLeaf", StressLeaf)
                    if CGCadjusted > ac_zero_threshold:
                        # CGCSF or CGCadjusted > 0
                        Crop_CCxAdjusted_temp = GetCrop_CCxAdjusted()
                        Crop_CCxAdjusted_temp = DetermineCCxAdjusted_Days(
                            Crop_CCxAdjusted_temp, CCxSF, CGCadjusted, tFinalCCx, VirtualTimeCC
                        )
                        SetCrop_CCxAdjusted(Crop_CCxAdjusted_temp)
                        if GetCrop_CCxAdjusted() < 0:
                            SetCCiActual(GetCCiPrev())
                        elif abs(GetCCiPrev() - 0.97999 * CCxSF) < 0.001:
                            SetCCiActual(CanopyCoverNoStressSF(
                                (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                                GetCrop_DaysToGermination(),
                                GetCrop_DaysToSenescence(),
                                GetCrop_DaysToHarvest(),
                                GetCrop_GDDaysToGermination(),
                                GetCrop_GDDaysToSenescence(),
                                GetCrop_GDDaysToHarvest(),
                                CCoTotal, CCxTotal, GetCrop_CGC(),
                                CDCTotal, GetCrop_GDDCGC(), GDDCDCTotal,
                                GetSimulation_SumGDD(), GetCrop_ModeCycle(),
                                GetSimulation_EffectStress_RedCGC(),
                                GetSimulation_EffectStress_RedCCX()
                            ))
                        else:
                            tTemp = RequiredTimeNew(
                                GetCCiPrev(),
                                GetCrop_CCoAdjusted(),
                                GetCrop_CCxAdjusted(),
                                CGCadjusted,
                                VirtualTimeCC
                            )
                            if tTemp < 0.0:
                                SetCCiActual(GetCCiPrev())
                            else:
                                tTemp = tTemp + 1.0
                                SetCCiActual(CCatTime(
                                    tTemp, GetCrop_CCoAdjusted(), CGCadjusted, GetCrop_CCxAdjusted()
                                ))
                    else:
                        # CGCadjusted = 0 - too dry for leaf expansion
                        SetCCiActual(GetCCiPrev())
                        if GetCCiActual() > GetCrop_CCoAdjusted():
                            SetCrop_CCoAdjusted(CCoTotal)
                        else:
                            SetCrop_CCoAdjusted(GetCCiActual())
                else:
                    SetCCiActual(CanopyCoverNoStressSF(
                        (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                        GetCrop_DaysToGermination(),
                        GetCrop_DaysToSenescence(),
                        GetCrop_DaysToHarvest(),
                        GetCrop_GDDaysToGermination(),
                        GetCrop_GDDaysToSenescence(),
                        GetCrop_GDDaysToHarvest(),
                        CCoTotal, CCxTotal, GetCrop_CGC(), CDCTotal,
                        GetCrop_GDDCGC(), GDDCDCTotal,
                        GetSimulation_SumGDD(), GetCrop_ModeCycle(),
                        GetSimulation_EffectStress_RedCGC(),
                        GetSimulation_EffectStress_RedCCX()
                    ))
                    SetCrop_CCoAdjusted(CCoTotal)
                    StressLeaf = -33.0  # maximum canopy is reached;
                    # no increase anymore of CGC after cutting
                if GetCCiActual() > CCxSFCD:
                    SetCCiActual(CCxSFCD)
                    StressLeaf = -33.0  # maximum canopy is reached;
                    # no increase anymore of CGC after cutting
            SetCrop_CCxAdjusted(GetCCiActual())

        # 3. Canopy can no longer develop (Mid-season (from tFinalCCx) or Late season stage)
        else:
            StressLeaf = -33.0  # maximum canopy is reached;
            if GetCrop_CCxAdjusted() < 0.0:
                SetCrop_CCxAdjusted(GetCCiPrev())

            if VirtualTimeCC < GetCrop_DaysToSenescence():  # mid-season
                # adjusted in 7.3 - 18 June 2025
                SetCCiActual(GetCrop_CCxAdjusted())
                """
                if GetCrop_CCxAdjusted() > 0.97999 * CCxSF:
                    SetCCiActual(CanopyCoverNoStressSF(
                        (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                        GetCrop_DaysToGermination(),
                        GetCrop_DaysToSenescence(),
                        GetCrop_DaysToHarvest(),
                        GetCrop_GDDaysToGermination(),
                        GetCrop_GDDaysToSenescence(),
                        GetCrop_GDDaysToHarvest(),
                        CCoTotal, CCxTotal, GetCrop_CGC(),
                        CDCTotal, GetCrop_GDDCGC(), GDDCDCTotal,
                        GetSimulation_SumGDD(), GetCrop_ModeCycle(),
                        GetSimulation_EffectStress_RedCGC(),
                        GetSimulation_EffectStress_RedCCX()
                    ))
                    SetCrop_CCxAdjusted(GetCCiActual())
                else:
                    SetCCiActual(CanopyCoverNoStressSF(
                        (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                        GetCrop_DaysToGermination(),
                        GetCrop_DaysToSenescence(),
                        GetCrop_DaysToHarvest(),
                        GetCrop_GDDaysToGermination(),
                        GetCrop_GDDaysToSenescence(),
                        GetCrop_GDDaysToHarvest(),
                        CCoTotal,
                        (GetCrop_CCxAdjusted() / (1.0 - GetSimulation_EffectStress_RedCCX() / 100.0)),
                        GetCrop_CGC(), CDCTotal, GetCrop_GDDCGC(),
                        GDDCDCTotal, GetSimulation_SumGDD(),
                        GetCrop_ModeCycle(),
                        GetSimulation_EffectStress_RedCGC(),
                        GetSimulation_EffectStress_RedCCX()
                    ))
                """
                if GetCCiActual() > CCxSFCD:
                    SetCCiActual(CCxSFCD)

            # late season
            else:
                StressSenescence = undef_int
                # to avoid display of zero stress in late season
                if GetCrop_CCxAdjusted() > CCxSFCD:
                    SetCrop_CCxAdjusted(CCxSFCD)
                if GetCrop_CCxAdjusted() < 0.01:
                    SetCCiActual(0.0)
                else:
                    # calculate CC in late season
                    # CCibis = CC which canopy declines
                    # (soil fertility/salinity stress) further in late season
                    CCibis = CCxSF - (GetSimulation_EffectStress_CDecline() / 100.0) * (
                        math.exp(2.0 * math.log((VirtualTimeCC + GetSimulation_DelayedDays() + 1.0) - GetCrop_DaysToFullCanopySF()))
                        / (GetCrop_DaysToSenescence() - GetCrop_DaysToFullCanopySF())
                    )
                    if CCibis < 0.0:
                        SetCCiActual(0.0)
                    else:
                        # CCiActual = CC with natural senescence in late season
                        Crop_CCxAdjusted_temp = GetCrop_CCxAdjusted()
                        CDCadjusted = GetCDCadjustedNoStressNew(CCxTotal, CDCTotal, Crop_CCxAdjusted_temp)
                        SetCrop_CCxAdjusted(Crop_CCxAdjusted_temp)
                        if (VirtualTimeCC + GetSimulation_DelayedDays() + 1) < (
                            GetCrop_DaysToSenescence() + LengthCanopyDecline(GetCrop_CCxAdjusted(), CDCadjusted)
                        ):
                            SetCCiActual(GetCrop_CCxAdjusted() * (
                                1.0 - 0.05 * (
                                    math.exp(((VirtualTimeCC + GetSimulation_DelayedDays() + 1) - GetCrop_DaysToSenescence())
                                             * 3.33 * CDCadjusted / (GetCrop_CCxAdjusted() + 2.29))
                                    - 1.0
                                )
                            ))
                            # CCiActual becomes CCibis, when canopy decline is more severe
                            if CCibis < GetCCiActual():
                                SetCCiActual(CCibis)
                        else:
                            SetCCiActual(0.0)

        # Adjustment for plant recovery upon rewatering (dormant period) 
        # ONLY when crop has still the potential for vegetative growth
        # 4. Canopy senescence due to water stress ?
        if (VirtualTimeCC < GetCrop_DaysToSenescence()) or (TimeSenescence > 0.0):
            # in late season with ongoing early senesence
            # (TimeSenescence in days)
            StressSenescence = 0.0
            WithBeta = True
            Crop_pSenAct_temp = GetCrop_pSenAct()
            Crop_pSenAct_temp = AdjustpSenescenceToETo(GetETo(), TimeSenescence, WithBeta, Crop_pSenAct_temp)
            SetCrop_pSenAct(Crop_pSenAct_temp)
            KsRED = 1.0  # effect of soil salinity on the
            # threshold for senescence
            if GetSimulation_SWCtopSoilConsidered():
                # top soil is relative wetter than total root zone
                if ((GetRootZoneWC_ZtopAct()
                        < (GetRootZoneWC_ZtopFC()
                            - GetCrop_pSenAct() * KsRED
                            * (GetRootZoneWC_ZtopFC() - GetRootZoneWC_ZtopWP())))
                    and (not GetSimulation_ProtectedSeedling())):
                    TheSenescenceON = True
                else:
                    TheSenescenceON = False
            else:
                if ((GetRootZoneWC_Actual()
                        < (GetRootZoneWC_FC()
                            - GetCrop_pSenAct() * KsRED
                            * (GetRootZoneWC_FC() - GetRootZoneWC_WP())))
                    and (not GetSimulation_ProtectedSeedling())):
                    TheSenescenceON = True
                else:
                    TheSenescenceON = False

            if TheSenescenceON:
                # CanopySenescence
                SetSimulation_EvapLimitON(True)
                # consider withered crop when not yet in late season
                if abs(TimeSenescence) < epsilon(0.0):
                    SetCCiTopEarlySen(GetCCiActual())
                    # CC before canopy decline
                TimeSenescence = TimeSenescence + 1.0  # add 1 day
                CDCadjusted, KsSen, StressSenescence = DetermineCDCadjustedWaterStress(
                    CDCadjusted, KsSen, TimeSenescence, CDCTotal, CCxSFCD, CCxTotal
                )

                if GetCCiTopEarlySen() < 0.001:
                    if ((GetSimulation_SumEToStress() > GetCrop_SumEToDelaySenescence()) or (abs(GetCrop_SumEToDelaySenescence()) < epsilon(0.0)) or (VirtualTimeCC >= tFinalCCx)):
                        CCiSen = 0.0  # no crop anymore
                    else:
                        if CCdormant > GetCrop_CCo():
                            CCiSen = GetCrop_CCo() + (1.0 - GetSimulation_SumEToStress() / GetCrop_SumEToDelaySenescence()) * (CCdormant - GetCrop_CCo())
                        else:
                            CCiSen = GetCrop_CCo()
                else:
                    if (((TimeSenescence * CDCTotal * 3.33) / (GetCCiTopEarlySen() + 2.29) > 100.0)
                        or (GetCCiPrev() >= 1.05 * GetCCiTopEarlySen())):
                        # Ln of negative or zero value
                        if ((GetSimulation_SumEToStress() > GetCrop_SumEToDelaySenescence()) or (abs(GetCrop_SumEToDelaySenescence()) < epsilon(0.0)) or (VirtualTimeCC >= tFinalCCx)):
                            CCiSen = 0.0  # no crop anymore
                        else:
                            if CCdormant > GetCrop_CCo():
                                CCiSen = GetCrop_CCo() + (1.0 - GetSimulation_SumEToStress() / GetCrop_SumEToDelaySenescence()) * (CCdormant - GetCrop_CCo())
                            else:
                                CCiSen = GetCrop_CCo()
                    else:
                        # CDC is adjusted to degree of stress
                        # time required to reach CCiprev with CDCadjusted
                        if abs(GetCCiTopEarlySen()) < epsilon(0.0):
                            SetCCiTopEarlySen(epsilon(1.0))
                        if abs(CDCadjusted) < epsilon(0.0):
                            CDCadjusted = epsilon(1.0)
                        tTemp = math.log(
                            1.0 + (1.0 - GetCCiPrev() / GetCCiTopEarlySen()) / 0.05
                        ) / (CDCadjusted * 3.33 / (GetCCiTopEarlySen() + 2.29))
                        # add 1 day to tTemp and calculate CCiSen
                        # with CDCadjusted
                        CCiSen = GetCCiTopEarlySen() * (
                            1.0 - 0.05 * (math.exp((tTemp + 1.0) * CDCadjusted * 3.33 / (GetCCiTopEarlySen() + 2.29)) - 1.0)
                        )

                    if CCiSen < 0.0:
                        CCiSen = 0.0
                    if ((GetCrop_SumEToDelaySenescence() > 0.0) and (GetSimulation_SumEToStress() <= GetCrop_SumEToDelaySenescence()) and (VirtualTimeCC < tFinalCCx)):
                        if (CCiSen < GetCrop_CCo()) or (CCiSen < CCdormant):
                            if CCdormant > GetCrop_CCo():
                                CCiSen = GetCrop_CCo() + (1.0 - GetSimulation_SumEToStress() / GetCrop_SumEToDelaySenescence()) * (CCdormant - GetCrop_CCo())
                            else:
                                CCiSen = GetCrop_CCo()

                if VirtualTimeCC < GetCrop_DaysToSenescence():
                    # before late season
                    if CCiSen > CCxSFCD:
                        CCiSen = CCxSFCD
                    SetCCiActual(CCiSen)
                    if GetCCiActual() > GetCCiPrev():
                        SetCCiActual(GetCCiPrev())
                        # to avoid jump in CC
                    # when CGCadjusted increases as a result of watering
                    SetCrop_CCxAdjusted(GetCCiActual())
                    if GetCCiActual() < CCoTotal:
                        SetCrop_CCoAdjusted(GetCCiActual())
                    else:
                        SetCrop_CCoAdjusted(CCoTotal)
                else:
                    # in late season
                    if CCiSen < GetCCiActual():
                        SetCCiActual(CCiSen)

                if (roundc(10000.0 * CCiSen, mold=1) <= (10000.0 * CCdormant)) or (
                    roundc(10000.0 * CCiSen, mold=1) <= roundc(10000.0 * GetCrop_CCo(), mold=1)
                ):
                    SetSimulation_SumEToStress(GetSimulation_SumEToStress() + GetETo())
            else:
                # no water stress, resulting in canopy senescence
                TimeSenescence = 0.0
                # No early senescence or back to normal
                StressSenescence = 0.0
                SetSimulation_SumEToStress(0.0)
                if (VirtualTimeCC > GetCrop_DaysToSenescence()) and (GetCCiActual() > GetCCiPrev()):
                    # result of a rewatering in late season of
                    # an early declining canopy
                    Crop_CCxAdjusted_temp = GetCrop_CCxAdjusted()
                    Crop_CCxAdjusted_temp, CDCadjusted = GetNewCCxandCDC(
                        GetCCiPrev(), CDCTotal, CCxSF, Crop_CCxAdjusted_temp, CDCadjusted, VirtualTimeCC
                    )
                    SetCrop_CCxAdjusted(Crop_CCxAdjusted_temp)
                    SetCCiActual(CanopyCoverNoStressSF(
                        (VirtualTimeCC + GetSimulation_DelayedDays() + 1),
                        GetCrop_DaysToGermination(),
                        GetCrop_DaysToSenescence(),
                        GetCrop_DaysToHarvest(),
                        GetCrop_GDDaysToGermination(),
                        GetCrop_GDDaysToSenescence(),
                        GetCrop_GDDaysToHarvest(),
                        CCoTotal,
                        (GetCrop_CCxAdjusted() / (1.0 - GetSimulation_EffectStress_RedCCX() / 100.0)),
                        GetCrop_CGC(), CDCadjusted,
                        GetCrop_GDDCGC(), GDDCDCTotal,
                        GetSimulation_SumGDD(), GetCrop_ModeCycle(),
                        GetSimulation_EffectStress_RedCGC(),
                        GetSimulation_EffectStress_RedCCX()
                    ))

        # 5. Adjust GetCrop().CCxWithered - required for correction
        # of Transpiration of dying green canopy
        if GetCCiActual() > GetCrop_CCxWithered():
            SetCrop_CCxWithered(GetCCiActual())

        # 6. correction for late-season stage for rounding off errors
        if VirtualTimeCC > GetCrop_DaysToSenescence():
            if GetCCiActual() > GetCCiPrev():
                SetCCiActual(GetCCiPrev())

        # 7. no crop as a result of fertiltiy and/or water stress
        if roundc(1000.0 * GetCCiActual(), mold=1) <= 0:
            NoMoreCrop = True

        # test
        TESTVAL = CGCadjusted

    return StressLeaf, StressSenescence, TimeSenescence, NoMoreCrop, TESTVAL

def DetermineCGCadjusted(CGCadjusted, CGCSF, pLeafLLAct):
    Wrelative = 0.0
    KsLeaf = 0.0
    SWCeffectiveRootZone = 0.0
    FCeffectiveRootZone = 0.0
    WPeffectiveRootZone = 0.0

    # determine FC and PWP
    if GetSimulation_SWCtopSoilConsidered():
        # top soil is relative wetter than total root zone
        SWCeffectiveRootZone = GetRootZoneWC_ZtopAct()
        Wrelative = (GetRootZoneWC_ZtopFC() - GetRootZoneWC_ZtopAct()) / (GetRootZoneWC_ZtopFC() - GetRootZoneWC_ZtopWP())
        FCeffectiveRootZone = GetRootZoneWC_ZtopFC()
        WPeffectiveRootZone = GetRootZoneWC_ZtopWP()
    else:
        # total rootzone is wetter than top soil
        SWCeffectiveRootZone = GetRootZoneWC_Actual()
        Wrelative = (GetRootZoneWC_FC() - GetRootZoneWC_Actual()) / (GetRootZoneWC_FC() - GetRootZoneWC_WP())
        FCeffectiveRootZone = GetRootZoneWC_FC()
        WPeffectiveRootZone = GetRootZoneWC_WP()

    # Canopy stress and effect of soil water stress on CGC
    global StressLeaf
    if SWCeffectiveRootZone >= FCeffectiveRootZone:
        CGCadjusted = CGCSF
        StressLeaf = 0.0
    elif SWCeffectiveRootZone <= WPeffectiveRootZone:
        CGCadjusted = 0.0
        StressLeaf = 100.0
    else:
        if Wrelative <= GetCrop_pLeafAct():
            CGCadjusted = CGCSF
            StressLeaf = 0.0
        elif Wrelative >= pLeafLLAct:
            CGCadjusted = 0.0
            StressLeaf = 100.0
        else:
            KsLeaf = KsAny(Wrelative, GetCrop_pLeafAct(), pLeafLLAct, GetCrop_KsShapeFactorLeaf())
            CGCadjusted = CGCSF * KsLeaf
            StressLeaf = 100.0 * (1.0 - KsLeaf)

    return CGCadjusted

def DetermineCDCadjustedWaterStress(CDCadjusted, KsSen, TimeSenescence, CDCTotal, CCxSFCD, CCxTotal):
    Wrelative = 0.0
    pSenLL = 0.0
    pSenAct = 0.0
    WithBeta = False

    pSenLL = 0.999  # WP
    if GetSimulation_SWCtopSoilConsidered():
        # top soil is relative wetter than total root zone
        Wrelative = (GetRootZoneWC_ZtopFC() - GetRootZoneWC_ZtopAct()) / (GetRootZoneWC_ZtopFC() - GetRootZoneWC_ZtopWP())
        # top soil
    else:
        Wrelative = (GetRootZoneWC_FC() - GetRootZoneWC_Actual()) / (GetRootZoneWC_FC() - GetRootZoneWC_WP())
        # total root zone
    WithBeta = False
    pSenAct = AdjustpSenescenceToETo(GetETo(), TimeSenescence, WithBeta, pSenAct)
    if Wrelative <= pSenAct:
        CDCadjusted = 0.001  # extreme small decline
        StressSenescence = 0.0
        KsSen = 1.0
    elif Wrelative >= pSenLL:
        CDCadjusted = CDCTotal * (CCxSFCD + 2.29) / (CCxTotal + 2.29)
        # full speed
        StressSenescence = 100.0
        KsSen = 0.0
    else:
        KsSen = KsAny(Wrelative, pSenAct, pSenLL, GetCrop_KsShapeFactorSenescence())
        if KsSen > ac_zero_threshold:
            CDCadjusted = CDCTotal * ((CCxSFCD + 2.29) / (CCxTotal + 2.29)) * (1.0 - math.exp(8.0 * math.log(KsSen)))
            StressSenescence = 100.0 * (1.0 - KsSen)
        else:
            CDCadjusted = 0.0
            StressSenescence = 0.0

    return CDCadjusted, KsSen, StressSenescence

def RequiredTimeNew(CCiToFind, CCo, CCx, CGCadjusted, VirtualTimeCC):
    CGCx = 0.0

    # Only when VirtualTime > 1
    # and CCx < CCiToFind
    # 1. CGCx to reach CCiToFind on previous day (= VirtualTime -1 )
    if CCiToFind <= (CCx / 2.0):
        CGCx = math.log(CCiToFind / CCo) / VirtualTimeCC
    else:
        CGCx = math.log((0.25 * CCx * CCx / CCo) / (CCx - CCiToFind)) / VirtualTimeCC
    # 2. Required time
    return VirtualTimeCC * CGCx / CGCadjusted

def CCatTime(tfictive, CCoGiven, CGCGiven, CCxGiven):
    CCi = 0.0

    CCi = CCoGiven * math.exp(CGCGiven * tfictive)
    if CCi > (CCxGiven / 2.0):
        CCi = CCxGiven - 0.25 * (CCxGiven / CCoGiven) * CCxGiven * math.exp(-CGCGiven * tfictive)
    return CCi



def GetNewCCxandCDC(CCiPrev, CDC, CCx, CCxAdjusted, CDCadjusted, VirtualTimeCC):
    CCxAdjusted = CCiPrev / (
        1.0
        - 0.05
        * (
            math.exp(
                (VirtualTimeCC - GetCrop_DaysToSenescence())
                * CDC
                * 3.33
                / (CCx + 2.29)
            )
            - 1.0
        )
    )
    # CDCadjusted := CDC * CCxAdjusted/CCx;
    CDCadjusted = CDC * (CCxAdjusted + 2.29) / (CCx + 2.29)
    return CCxAdjusted, CDCadjusted
    

def FeedbackCC():
    # canopy is still developing
    if ((GetCCiActual() - GetCCiPrev()) > 0.005) and (GetTact() == 0.0):
        # due to aeration stress or ETo = 0
        SetCCiActual(GetCCiPrev())
        # no transpiration, no crop developmentc

def HorizontalInflowGWTable(DepthGWTmeter, HorizontalSaltFlow, HorizontalWaterFlow):
    Ztot = 0.0
    Zi = 0.0
    DeltaTheta = 0.0
    SaltAct = 0.0
    SaltAdj = 0.0
    compi = 0
    celli = 0
    Compi_temp = None

    Ztot = 0.0
    for compi in range(1, int(GetNrCompartments()) + 1):
        Ztot = Ztot + GetCompartment_Thickness(compi)
        Zi = Ztot - GetCompartment_Thickness(compi) / 2.0
        if Zi >= DepthGWTmeter:
            # soil water content is at saturation
            if GetCompartment_theta(compi) < (GetSoilLayer_SAT(GetCompartment_Layer(compi)) / 100.0):
                DeltaTheta = (GetSoilLayer_SAT(GetCompartment_Layer(compi)) / 100.0) - GetCompartment_theta(compi)
                SetCompartment_theta(compi, GetSoilLayer_SAT(GetCompartment_Layer(compi)) / 100.0)
                HorizontalWaterFlow = HorizontalWaterFlow + 1000.0 * DeltaTheta * GetCompartment_Thickness(compi) * (
                    1.0 - GetSoilLayer_GravelVol(GetCompartment_Layer(compi)) / 100.0
                )

            # ECe is equal to the EC of the groundwater table
            if abs(ECeComp(GetCompartment_i(compi)) - GetECiAqua()) > 0.0001:
                SaltAct = 0.0
                for celli in range(1, int(GetSoilLayer_SCP1(GetCompartment_Layer(compi))) + 1):
                    SaltAct = SaltAct + (GetCompartment_Salt(compi, celli) + GetCompartment_Depo(compi, celli)) / 100.0  # Mg/ha

                Compi_temp = GetCompartment_i(compi)
                DetermineSaltContent(GetECiAqua(), Compi_temp)
                SetCompartment_i(compi, Compi_temp)

                SaltAdj = 0.0
                for celli in range(1, int(GetSoilLayer_SCP1(GetCompartment_Layer(compi))) + 1):
                    SaltAdj = SaltAdj + (GetCompartment_Salt(compi, celli) + GetCompartment_Depo(compi, celli)) / 100.0  # Mg/ha

                HorizontalSaltFlow = HorizontalSaltFlow + (SaltAdj - SaltAct)

    return HorizontalSaltFlow, HorizontalWaterFlow



def BUDGET_module(dayi, TargetTimeVal, TargetDepthVal, VirtualTimeCC, SumInterval, DayLastCut, NrDayGrow, Tadj, GDDTadj, GDDayi, CGCref, GDDCGCref, CO2i, CCxTotal, CCoTotal, CDCTotal, GDDCDCTotal, SumGDDadjCC, Coeffb0Salt, Coeffb1Salt, Coeffb2Salt, StressTotSaltPrev, DayFraction, GDDayFraction, FracAssim, StressSFadjNEW, StorageON, MobilizationON, StressLeaf, StressSenescence, TimeSenescence, NoMoreCrop, TESTVAL):
    control = 0
    InfiltratedRain = 0
    InfiltratedIrrigation = 0
    InfiltratedStorage = 0
    EpotTot = 0
    SubDrain = 0
    DAP = 0
    ECInfilt = 0
    # ! EC of the infiltrated water (surface storage)
    WaterTableInProfile = False
    HorizontalWaterFlow = 0
    HorizontalSaltFlow = 0
    SWCtopSoilConsidered_temp = False
    EvapWCsurf_temp = 0
    CRwater_temp = 0
    Tpot_temp = 0
    Epot_temp = 0
    Comp_temp = [0] * (int(max_No_compartments) + 1)  # 1-based
    Crop_pActStom_temp = 0
    CRsalt_temp = 0
    ECdrain_temp = 0
    Surf0_temp = 0
    TargetTimeVal_loc = 0
    StressSFadjNEW_loc = 0
    TargetTimeVal_loc = TargetTimeVal
    StressSFadjNEW_loc = StressSFadjNEW

    # 1. Soil water balance
    control = control_begin_day
    ECdrain_temp = GetECDrain()
    Surf0_temp = GetSurf0()

    Surf0_temp, ECInfilt, ECdrain_temp, HorizontalWaterFlow, HorizontalSaltFlow, SubDrain = CheckWaterSaltBalance(
        dayi,
        InfiltratedRain,
        control,
        InfiltratedIrrigation,
        InfiltratedStorage,
        Surf0_temp,
        ECInfilt,
        ECdrain_temp,
        HorizontalWaterFlow,
        HorizontalSaltFlow,
        SubDrain
    )

    SetECDrain(ECdrain_temp)
    SetSurf0(Surf0_temp)

    # 2. Adjustments in presence of Groundwater table
    WaterTableInProfile = CheckForWaterTableInProfile(GetZiAqua()/100., GetCompartment(), WaterTableInProfile)
    Comp_temp = GetCompartment()
    Comp_temp = CalculateAdjustedFC(GetZiAqua()/100., Comp_temp)
    SetCompartment(Comp_temp)

    # 3. Drainage
    calculate_drainage()
    # 4. Runoff
    if GetManagement_BundHeight() < 0.001:
        SetDaySubmerged(0)
        if (GetManagement_RunoffOn()) and (GetRain() > 0.1):
            calculate_runoff(GetSimulParam_RunoffDepth())

    # 5. Infiltration (Rain and Irrigation)
    if ((GetRainRecord_DataType() == datatype_Decadely)
            or (GetRainRecord_DataType() == datatype_Monthly)):
        SubDrain = CalculateEffectiveRainfall(SubDrain)

    if ((GetIrriMode() == IrriMode_Generate)
            and (GetIrriInfoLastDay() != undef_int)
            and (dayi >= (GetCrop_DayN() - GetIrriInfoLastDay() + 1))):
        SetIrrigation(0.0)
    else:
        if (((GetIrriMode() == IrriMode_Generate)
                and (GetIrrigation() == 0.0))
                and (TargetTimeVal_loc != -999)):
            SubDrain, TargetTimeVal_loc = Calculate_irrigation(
                SubDrain,
                TargetTimeVal_loc,
                TargetDepthVal,
            )
    if GetManagement_BundHeight() >= 0.01:
        InfiltratedRain, InfiltratedIrrigation, InfiltratedStorage, ECInfilt = calculate_surfacestorage(
            InfiltratedRain,
            InfiltratedIrrigation,
            InfiltratedStorage,
            ECInfilt,
            SubDrain,
            dayi
        )
    else:
        InfiltratedRain, InfiltratedIrrigation, InfiltratedStorage, SubDrain = calculate_Extra_runoff(
            InfiltratedRain,
            InfiltratedIrrigation,
            InfiltratedStorage,
            SubDrain
        )

    InfiltratedRain, InfiltratedIrrigation, InfiltratedStorage, SubDrain = calculate_infiltration(
        InfiltratedRain,
        InfiltratedIrrigation,
        InfiltratedStorage,
        SubDrain
    )


    # 6. Capillary Rise
    CRwater_temp = GetCRwater()
    CRsalt_temp = GetCRsalt()
    CRwater_temp, CRsalt_temp = calculate_CapillaryRise(CRwater_temp, CRsalt_temp)
    SetCRwater(CRwater_temp)
    SetCRsalt(CRsalt_temp)

    # 7. Salt balance
    calculate_saltcontent(InfiltratedRain, InfiltratedIrrigation, InfiltratedStorage, SubDrain, dayi)

    # 8. Check Germination
    if (not GetSimulation_Germinate()) and (dayi >= GetCrop_Day1()):
        CheckGermination()

    # 9. Determine effect of soil fertiltiy and soil salinity stress
    if not NoMoreCrop:
        EffectSoilFertilitySalinityStress(StressSFadjNEW_loc, Coeffb0Salt, Coeffb1Salt, Coeffb2Salt, NrDayGrow, StressTotSaltPrev, VirtualTimeCC)

    # 10. Canopy Cover (CC)
    if not NoMoreCrop:
        # determine water stresses affecting canopy cover
        SWCtopSoilConsidered_temp = GetSimulation_SWCtopSoilConsidered()
        SWCtopSoilConsidered_temp = DetermineRootZoneWC(GetRootingDepth(), SWCtopSoilConsidered_temp)
        SetSimulation_SWCtopSoilConsidered(SWCtopSoilConsidered_temp)
        # determine canopy cover
        # select case (GetCrop_ModeCycle())
        if (GetCrop_ModeCycle() == ModeCycle_GDDays):
            StressLeaf, StressSenescence, TimeSenescence, NoMoreCrop = DetermineCCiGDD(
                CCxTotal,
                CCoTotal,
                StressLeaf,
                FracAssim,
                MobilizationON,
                StorageON,
                SumGDDadjCC,
                VirtualTimeCC,
                StressSenescence,
                TimeSenescence,
                NoMoreCrop,
                CDCTotal,
                GDDayFraction,
                GDDayi,
                GDDCDCTotal,
                GDDTadj
            )
        else:
            StressLeaf, StressSenescence, TimeSenescence, NoMoreCrop, TESTVAL = DetermineCCi(
                CCxTotal,
                CCoTotal,
                StressLeaf,
                FracAssim,
                MobilizationON,
                StorageON,
                Tadj,
                VirtualTimeCC,
                StressSenescence,
                TimeSenescence,
                NoMoreCrop,
                CDCTotal,
                DayFraction,
                GDDCDCTotal,
                TESTVAL
            )

        # added 7.3 - premature end
        if dayi == GetSimulation_DayNrPrematureEnd():
            SetCCiActual(0.0)
            SetNoMoreCrop(True)

    # 11. Determine Tpot and Epot
    # 11.1 Days after Planting
    if GetCrop_ModeCycle() == ModeCycle_CalendarDays:
        DAP = VirtualTimeCC
    else:
        # growing degree days - to position correctly where in cycle
        DAP = SumCalendarDays(roundc(SumGDDadjCC, mold=1), GetCrop_Day1(), GetCrop_Tbase(), GetCrop_Tupper(), GetSimulParam_Tmin(), GetSimulParam_Tmax())
        DAP = DAP + GetSimulation_DelayedDays()
        # are not considered when working with GDDays

    # 11.2 Calculation
    Tpot_temp = GetTpot()
    Tpot_temp, EpotTot = CalculateETpot(DAP, GetCrop_DaysToGermination(), GetCrop_DaysToFullCanopy(), GetCrop_DaysToSenescence(), GetCrop_DaysToHarvest(), DayLastCut, GetCCiActual(), GetETo(), GetCrop_KcTop(), GetCrop_KcDeclineCumul(), GetCrop_CCxAdjusted(), GetCrop_CCxWithered(), float(GetCrop_CCEffectEvapLate()), CO2i, GDDayi, GetCrop_GDtranspLow(), Tpot_temp, EpotTot)
    SetTpot(Tpot_temp)
    SetEpot(EpotTot)
    # adjustment Epot for mulch and partial wetting in next step
    Crop_pActStom_temp = GetCrop_pActStom()
    Crop_pActStom_temp = AdjustpStomatalToETo(GetETo(), Crop_pActStom_temp)
    SetCrop_pActStom(Crop_pActStom_temp)

    # 12. Evaporation
    if not GetPreDay():
        PrepareStage2()
        # Initialize Simulation.EvapstartStg2 (REW is gone)
    if (GetRain() > 0.) or ((GetIrrigation() > 0.) and (GetIrriMode() != IrriMode_Inet)):
        PrepareStage1()
    EvapWCsurf_temp = GetSimulation_EvapWCsurf()
    Epot_temp = GetEpot()
    Epot_temp, EvapWCsurf_temp = AdjustEpotMulchWettedSurface(
        dayi,
        EpotTot,
        Epot_temp,
        EvapWCsurf_temp
    )
    SetEpot(Epot_temp)
    SetSimulation_EvapWCsurf(EvapWCsurf_temp)
    if ((GetRainRecord_DataType() == datatype_Decadely) or (GetRainRecord_DataType() == datatype_Monthly)) and (GetSimulParam_EffectiveRain_RootNrEvap() > 0):
        # reduction soil evaporation
        SetEpot(GetEpot() * (math.exp((1./GetSimulParam_EffectiveRain_RootNrEvap()) * math.log((GetSoil_REW()+1.)/20.))))
    # actual evaporation
    SetEact(0.)
    if GetEpot() > 0.:
        # surface water
        if GetSurfaceStorage() > 0.:
            CalculateEvaporationSurfaceWater()
        # stage 1 evaporation
        if (abs(GetEpot() - GetEact()) > ac_zero_threshold) and (GetSimulation_EvapWCsurf() > 0.):
            CalculateSoilEvaporationStage1()
        # stage 2 evaporation
        if abs(GetEpot() - GetEact()) > ac_zero_threshold:
            CalculateSoilEvaporationStage2()
    # Reset redcution Epot for 10-day or monthly rainfall data
    if ((GetRainRecord_DataType() == datatype_Decadely) or (GetRainRecord_DataType() == datatype_Monthly)) and (GetSimulParam_EffectiveRain_RootNrEvap() > 0.):
        SetEpot(GetEpot() / (math.exp((1./GetSimulParam_EffectiveRain_RootNrEvap()) * math.log((GetSoil_REW()+1.)/20.))))


    # 13. Transpiration
    if (not NoMoreCrop) and (GetRootingDepth() > 0.0001):
        if (GetSurfaceStorage() > 0.) and ((GetCrop_AnaeroPoint() == 0) or (GetDaySubmerged() < GetSimulParam_DelayLowOxygen())):
            surface_transpiration(Coeffb0Salt, Coeffb1Salt, Coeffb2Salt)
        else:
            calculate_transpiration(GetTpot(), Coeffb0Salt, Coeffb1Salt, Coeffb2Salt)
    if GetSurfaceStorage() < epsilon(0.):
        SetDaySubmerged(0)
    FeedbackCC()


    # 14. Adjustment to groundwater table)
    if WaterTableInProfile:
        HorizontalSaltFlow, HorizontalWaterFlow = HorizontalInflowGWTable(GetZiAqua()/100., HorizontalSaltFlow, HorizontalWaterFlow)

    # 15. Salt concentration
    ConcentrateSalts()

    # 16. Soil water balance
    control = control_end_day
    ECdrain_temp = GetECDrain()
    Surf0_temp = GetSurf0()

    Surf0_temp, ECInfilt, ECdrain_temp, HorizontalWaterFlow, HorizontalSaltFlow, SubDrain = CheckWaterSaltBalance(
        dayi,
        InfiltratedRain,
        control,
        InfiltratedIrrigation,
        InfiltratedStorage,
        Surf0_temp,
        ECInfilt,
        ECdrain_temp,
        HorizontalWaterFlow,
        HorizontalSaltFlow,
        SubDrain
    )

    SetECDrain(ECdrain_temp)
    SetSurf0(Surf0_temp)

    return (StressLeaf, StressSenescence, TimeSenescence, NoMoreCrop, TESTVAL)

# -----------------------------------------------------------------------------
# end BUDGET_module
# -----------------------------------------------------------------------------
