const sessions = new Map();

function defaultGroupFlow() {
  return {
    step: "name",
    name: "", count: 1, numbering: true, description: "",
    photo: null, disappearing: 0, members: [], makeAdmin: false,
    permissions: { sendMessages: true, editInfo: true, addMembers: true, approveMembers: false },
  };
}

function defaultFeatureFlow(feature) {
  return {
    feature,
    step: "group_type",
    allGroups: [],
    selectedIds: [],
    page: 0,
    keyword: "",
    adminNumbers: [],
    wordGroups: {},
  };
}

function defaultSession() {
  return {
    awaitingPhoneForIndex: null,
    groupFlow:    null,
    joinFlow:     null,
    featureFlow:  null,
    lastMsgId:    null,
    cancelMsgId:  null,
    cancelPending: false,
    awaitingVcf:  null,
    _lastActive:  Date.now(),
  };
}

function getSession(userId) {
  if (!sessions.has(userId)) sessions.set(userId, defaultSession());
  return sessions.get(userId);
}

function updateSession(userId, patch) {
  const cur = getSession(userId);
  sessions.set(userId, { ...cur, ...patch, _lastActive: Date.now() });
}

function resetSession(userId) {
  sessions.set(userId, defaultSession());
}

// Export sessions Map so external code can do periodic cleanup
module.exports = { sessions, getSession, updateSession, resetSession, defaultGroupFlow, defaultFeatureFlow };
