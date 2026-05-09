const sessions = new Map();

function defaultGroupFlow() {
  return {
    step: "name",
    name: "",
    count: 1,
    numbering: true,
    description: "",
    photo: null,
    disappearing: 0,
    members: [],
    makeAdmin: false,
    permissions: {
      sendMessages: true,
      editInfo: true,
      addMembers: true,
      approveMembers: false,
    },
  };
}

function defaultSession() {
  return {
    awaitingPhoneForIndex: null,
    groupFlow: null,
  };
}

function getSession(userId) {
  if (!sessions.has(userId)) sessions.set(userId, defaultSession());
  return sessions.get(userId);
}

function updateSession(userId, patch) {
  const s = getSession(userId);
  sessions.set(userId, { ...s, ...patch });
}

function resetSession(userId) {
  sessions.set(userId, defaultSession());
}

module.exports = { getSession, updateSession, resetSession, defaultGroupFlow };
