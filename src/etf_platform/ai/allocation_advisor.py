class AllocationAdvisor:
    """
    Recommends ETF allocation based on risk level.
    """

    def recommend(self, risk_level):
        risk_level = risk_level.upper()

        allocations = {
            "LOW": {
                "NIFTYBEES": 40,
                "GOLDBEES": 40,
                "LIQUIDBEES": 20,
            },
            "MEDIUM": {
                "NIFTYBEES": 60,
                "GOLDBEES": 20,
                "LIQUIDBEES": 20,
            },
            "HIGH": {
                "NIFTYBEES": 80,
                "GOLDBEES": 10,
                "LIQUIDBEES": 10,
            },
        }

        if risk_level not in allocations:
            raise ValueError(f"Unknown risk level: {risk_level}")

        return allocations[risk_level]
