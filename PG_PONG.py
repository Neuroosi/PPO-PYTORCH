import torch
from torch import nn
from torch._C import device
from torch import optim
import random
import numpy as np
from collections import deque
import gym
import math
from collections import deque
import skimage
import POLICY_NET
import VALUE_ESTIMATOR
import Transition
from wandb import wandb
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Using {device} device")
##Hyperparameters
learning_rate_policy = 0.00025
learning_rate_value = 0.001
GAMMA = 0.99
EPISODES = 5000
BATCH_SIZE = 5
INPUTSIZE = (84,84)
EPSILON = 0.2

def train_POLICYNET(states , actions, A, agent, old_agent, optimizer):
    pred = agent(states)
    old_pred = old_agent(states)
    actions = actions*A.unsqueeze(1)
    pred_ratio = torch.exp(pred- old_pred)
    clip = torch.clamp(pred_ratio, 1-EPSILON, 1+EPSILON)
    loss = -torch.mean(torch.min(pred_ratio*actions, clip*actions))
    optimizer.zero_grad()
    loss.backward()
    for param in agent.parameters():
        param.grad.data.clamp_(-1, 1)
    optimizer.step()
    return loss.item()

def train_ESTIMATORNET( states,  G, loss_fn, agent, optimizer):
    pred = agent(states)
    pred = torch.squeeze(pred)
    loss = loss_fn(pred, G)
    optimizer.zero_grad()
    loss.backward()
    for param in agent.parameters():
        param.grad.data.clamp_(-1, 1)
    optimizer.step()
    return loss.item()

def getFrame(x):
    x = x[25:210,0:160]
    state = skimage.color.rgb2gray(x)
    state = skimage.transform.resize(state, INPUTSIZE)
    state = skimage.exposure.rescale_intensity(state,out_range=(0,255))
    state = state.astype('uint8')
    return state

def makeState(state):
    return np.stack((state[0],state[1],state[2],state[3]), axis=0)

def saveModel(agent, filename):
    torch.save(agent.state_dict(), filename)
    print("Model saved!")

def loadModel(agent, filename):
    agent.load_state_dict(torch.load(filename))
    print("Model loaded!")

def predict_POLICY(agent, state, transition):
    with torch.no_grad():
        state = np.expand_dims(state, axis=0)
        prob = torch.exp(agent(torch.from_numpy(state).float()))
        cache =  torch.squeeze(prob)
        transition.probs.append(cache)
        prob = prob.cpu().detach().numpy()
        prob = np.squeeze(prob)
        return np.random.choice(2, p = prob)

def predict_VALUE(agent, state):
    with torch.no_grad():
        return agent(state)

if __name__ == "__main__":
    env = gym.make("PongDeterministic-v4")
    ##Book keeping
    VALUE_ESTIMATOR_LOSS = []
    POLICY_LOSS = []
    state = deque(maxlen = 4)
    wandb.init(project="PPO_PONG", entity="neuroori") 
    ##Actors in the simulation
    updater_agent = POLICY_NET.NeuralNetwork(2).to(device)
    actor_agent = POLICY_NET.NeuralNetwork(2).to(device)
    actor_agent.load_state_dict(updater_agent.state_dict())
    value_estimator = VALUE_ESTIMATOR.NeuralNetwork().to(device)
    ##Optimization stuff
    loss_VALUE = torch.nn.HuberLoss()
    optimizer_POLICY = optim.Adam(updater_agent.parameters(), lr = learning_rate_policy)
    optimizer_VALUE = optim.Adam(value_estimator.parameters(), lr = learning_rate_value)
    ##Transition class
    transition = Transition.Transition(2)

    ans = input("Use a pretrained model y/n? ")
    if ans == "y":
        loadModel(actor_agent, "POLICY_WEIGHTS.pth")
        loadModel(updater_agent, "POLICY_WEIGHTS.pth")
        loadModel(value_estimator, "VALUE_WEIGHTS.pth")
    
    total_time = 0
    batch_steps = 0

    for episode in range(1,EPISODES+500000000000):
        observation = env.reset()
        state.append(getFrame(observation))
        state.append(getFrame(observation))
        state.append(getFrame(observation))
        state.append(getFrame(observation))
        gamereward = 0
        games_played = 0
        batch_reward = 0
        while batch_steps < 5000:
            action = predict_POLICY(actor_agent, makeState(state)/255, transition)
            if action == 0:
                observation, reward, done, info = env.step(2)##UP
            else:
                observation, reward, done, info = env.step(3)##DOWN
            transition.addTransition(makeState(state), reward, action)
            state.append(getFrame(observation))
            total_time += 1
            batch_steps += 1
            gamereward += reward
            env.render()
            if done:
                print("Running reward: ", gamereward)
                batch_reward += gamereward
                gamereward = 0
                observation = env.reset()
                state.append(getFrame(observation))
                state.append(getFrame(observation))
                state.append(getFrame(observation))
                state.append(getFrame(observation))
                games_played += 1

        if games_played > 0:
            print("Batch running reward: ", batch_reward/games_played, " Episode: ", episode, " Steps: ", total_time)
        else:
            print("Batch running reward: ", gamereward, " Episode: ", episode, " Steps: ", total_time)
        ##Put data to a tensor form
        G = transition.discounted_reward(GAMMA)
        G = torch.from_numpy(G).to(device).float()
        states = [torch.from_numpy(np.array(state)/255) for state in transition.states]
        states = torch.stack(states)
        states = states.float()
        actions = [torch.from_numpy(np.array(action)) for action in transition.actions]
        actions = torch.stack(actions)
        actions = actions.float()
        ##TRAIN
        V_ESTIMATES = torch.squeeze(predict_VALUE(value_estimator, states)).float()
        loss_policy = train_POLICYNET(states.to(device), actions.to(device),  (G-V_ESTIMATES).to(device), updater_agent, actor_agent, optimizer_POLICY)
        loss_value = train_ESTIMATORNET(states, G, loss_VALUE, value_estimator, optimizer_VALUE)
        print(loss_policy)
        print(loss_value)
        POLICY_LOSS.append(loss_policy)
        VALUE_ESTIMATOR_LOSS.append(loss_value)
        if games_played > 0:
            wandb.log({"BATCH REWARD": batch_reward/games_played})
        else:
            wandb.log({"BATCH REWARD": gamereward})
        games_played = 0
        cumureward = 0
        batch_steps = 0
        transition.resetTransitions()
        actor_agent.load_state_dict(updater_agent.state_dict())
        if total_time % 100000 == 0:
            saveModel(actor_agent, "POLICY_WEIGHTS.pth")
            saveModel(value_estimator, "VALUE_WEIGHTS.pth")