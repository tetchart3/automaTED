import tensorflow as tf
import tensorflow.contrib.slim as slim
import numpy as np

from ohtextloader import TextLoader
from templatemanager import TemplateManager
from tensorflow.python.ops import rnn_cell
import tensorflow.contrib.legacy_seq2seq as seq2seq # I don't want to use legacy...
from goru.goru import GORUCell

class WordRNN():

    def __init__(self, restore):
        # -------------------------------------------
        # Global variables

        self.batch_size = 10
        self.sequence_length = 10
        self.state_dim = 256
        self.profile_size = 14
        self.num_layers = 2
        self.data_loader = TextLoader(".", self.batch_size, self.sequence_length)
        self.vocab_size = self.data_loader.vocab_size  # dimension of one-hot encodings

        tf.reset_default_graph()
        self.createGraph()

        self.sess = tf.Session()
        self.path = "./word_tf_logs"
        self.sess.run(tf.global_variables_initializer())
        self.summary_writer = tf.summary.FileWriter(self.path)
        self.saver = tf.train.Saver()
        if restore:
            self.saver.restore(self.sess, self.path + "/model.ckpt")


    def createGraph(self):
        # -------------------------------------------
        # Inputs

        self.in_ph = tf.placeholder(tf.int32, [self.batch_size, self.sequence_length], name='inputs')
        self.targ_ph = tf.placeholder(tf.int32, [self.batch_size, self.sequence_length], name='targets')
        in_onehot = tf.one_hot(self.in_ph, self.vocab_size, name="input_onehot")
        self.profile = tf.placeholder(tf.float32, [self.batch_size, self.profile_size], name="profile")

        inputs = tf.split(in_onehot, self.sequence_length, 1)
        inputs = [tf.squeeze(input_, [1]) for input_ in inputs]
        targets = tf.split(self.targ_ph, self.sequence_length, 1)

        # -------------------------------------------
        # Computation Graph

        with tf.variable_scope("RNN"):
            # define cells
            cells = [rnn_cell.GRUCell(self.state_dim) for i in range(self.num_layers)]
            # cells = [GORUCell(state_dim, str(i)) for i in range(num_layers)]

            # stack and initialize cells
            stacked_cells = rnn_cell.MultiRNNCell(cells, state_is_tuple=True)
            self.initial_state = stacked_cells.zero_state(self.batch_size, tf.float32)

            # mix in profile vector
            inputs = [tf.concat([i, self.profile], 1) for i in inputs]
            W_mix = tf.get_variable( "W_mix", [self.vocab_size + 14, self.vocab_size], tf.float32,
                                        tf.random_normal_initializer( stddev=0.02 ) )
            b_mix = tf.get_variable( "b_mix", [self.vocab_size],
                                        initializer=tf.constant_initializer( 0.0 ))
            mixed_inputs = [tf.nn.relu(tf.matmul( i, W_mix ) + b_mix) for i in inputs]

            # call seq2seq.rnn_decoder
            outputs, self.final_state = seq2seq.rnn_decoder(mixed_inputs, self.initial_state, stacked_cells)
            
            # transform the list of state outputs to a list of logits.
            # use a linear transformation.
            W = tf.get_variable("W", [self.state_dim, self.vocab_size], tf.float32,
                                        tf.random_normal_initializer( stddev=0.02))
            b = tf.get_variable("b", [self.vocab_size],
                                        initializer=tf.constant_initializer(0.0))
            logits = [tf.matmul(o, W) + b for o in outputs]

            # call seq2seq.sequence_loss
            const_weights = [tf.ones([self.batch_size]) for i in xrange(self.sequence_length)]
            self.loss = seq2seq.sequence_loss(logits, targets, const_weights)
            self.loss_summary = tf.summary.scalar("loss", self.loss)

            # create a training op using the Adam optimizer
            self.optim = tf.train.AdamOptimizer(0.001, beta1=0.5).minimize(self.loss)


        # -------------------------------------------
        # Sampler Graph

        with tf.variable_scope("RNN", reuse=True):
            # sample one at a time
            batch_size = 1

            # inputs
            self.s_inputs = tf.placeholder(tf.int32, [batch_size], name='s_inputs')
            s_onehot = tf.one_hot(self.s_inputs, self.vocab_size, name="s_input_onehot")
            self.s_profile = tf.placeholder(tf.float32, [batch_size, self.profile_size], name="s_profile")

            # initialize
            self.s_initial_state = stacked_cells.zero_state(batch_size, tf.float32)

            # mix in profile vector
            s_onehot = tf.concat([s_onehot, self.s_profile], 1)
            s_mixed_inputs = tf.nn.relu(tf.matmul(s_onehot, W_mix) + b_mix)

            # call seq2seq.rnn_decoder
            s_outputs, self.s_final_state = seq2seq.rnn_decoder([s_mixed_inputs],
                                                self.s_initial_state, stacked_cells)

            # transform the list of state outputs to a list of logits.
            # use a linear transformation.
            s_outputs = tf.reshape(s_outputs, [1, self.state_dim])
            self.s_probs = tf.nn.softmax(tf.matmul(s_outputs, W) + b)


    def sample_probs(self, num, prime):
        # prime the pump
        sample_profile = np.array([1.0] * 14)
        sample_profile[3] = 100.0
        sample_profile /= np.sum(sample_profile)
        sample_profile = sample_profile.reshape((1, 14))

        # generate an initial state. this will be a list of states, one for
        # each layer in the multicell.
        s_state = self.sess.run(self.s_initial_state)

        # for each character, feed it into the sampler graph and
        # update the state.
        for word in prime.split(" "):
            x = np.ravel(self.data_loader.vocab[word]).astype('int32')
            feed = {self.s_inputs:x, self.s_profile:sample_profile}
            for i, s in enumerate(self.s_initial_state):
                feed[s] = s_state[i]
            s_state = self.sess.run(self.s_final_state, feed_dict=feed)

        # now we have a primed state vector; we need to start sampling.
        total_probs = []
        for n in range(num):
            x = np.ravel(self.data_loader.vocab[word]).astype('int32')

            # plug the most recent character in...
            feed = {self.s_inputs:x, self.s_profile:sample_profile}
            for i, s in enumerate(self.s_initial_state):
                feed[s] = s_state[i]
            ops = [self.s_probs]
            ops.extend(list(self.s_final_state))

            retval = self.sess.run(ops, feed_dict=feed)

            s_probsv = retval[0]
            s_state = retval[1:]

            total_probs.append(s_probsv[0])
        return total_probs


    def sample(self, num, prime):
        probs = self.sample_probs(num, prime)
        ret = prime
        for p in probs:
            sample = np.random.choice(self.vocab_size, p=p)
            word = self.data_loader.vocab_list[sample]
            ret += " " + word
        return ret


    def sample_with_template(self, num, prime):
        # create template
        tm = TemplateManager()
        tm.generate_template(length=num + len(prime.split()))
        while not tm.match(sentence=prime):
            print("The prime won't work with the template! Generating new template...")
            tm.generate_template(length=num)
        # sample words
        probs = self.sample_probs(num, prime)
        ret = prime
        for p in probs:
            valid = False
            while not valid:
                sample = np.random.choice(self.vocab_size, p=p)
                word = self.data_loader.vocab_list[sample]
                temp_ret = ret + " " + word
                valid = tm.match_latest(sentence=temp_ret)
                # renormalize
                p[sample] = 0.0
                p /= np.sum(p)
            ret = temp_ret
        return tm.format_sentence(ret)


    def train(self):
        lts = []
        print("FOUND %d BATCHES" % self.data_loader.num_batches)

        for j in range(1000):

            state = self.sess.run(self.initial_state)
            self.data_loader.reset_batch_pointer()

            for i in range(self.data_loader.num_batches): 
                x, y, profile_vec = self.data_loader.next_batch()

                # we have to feed in the individual states of the MultiRNN cell
                feed = {self.in_ph: x, self.targ_ph: y, self.profile: profile_vec}
                for k, s in enumerate(self.initial_state):
                    feed[s] = state[k]

                ops = [self.optim, self.loss]
                ops.extend(list(self.final_state))

                # retval will have at least 3 entries:
                # 0 is None (triggered by the optim op)
                # 1 is the loss
                # 2+ are the new final states of the MultiRNN cell
                retval, loss_summary = self.sess.run([ops, self.loss_summary], feed_dict=feed)
                self.summary_writer.add_summary(loss_summary, (j * self.data_loader.num_batches) + i)

                lt = retval[1]
                state = retval[2:]

                if i%100==0:
                    print "%d %d\t%.4f" % (j, i, lt) 
                    lts.append( lt )
                    # print self.sample(num=160)
                    print self.sample_with_template(num=49, prime='he')

            print("Completed epoch: {}, saving weights...".format(j))
            self.saver.save(self.sess, self.path + "model.ckpt")

        self.summary_writer.close()
        # plot the loss
        plt.plot(range(len(lts)), lts)
        plt.show()



if __name__ == "__main__":
    # sess = tf.Session()
    wordRNN = WordRNN(restore=False)
    wordRNN.train()