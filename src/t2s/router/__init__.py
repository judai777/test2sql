"""路由层出口：应用入口编排 + 意图/权限。"""
from t2s.router.intents import classify_intent, is_dangerous
from t2s.router.permissions import PermissionDecision, PermissionRegistry
from t2s.router.service import RouterService

__all__ = ["RouterService", "classify_intent", "is_dangerous",
           "PermissionRegistry", "PermissionDecision"]
